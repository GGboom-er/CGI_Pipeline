"""Area-sampled surface F-score; libigl owns all closest-surface queries."""
from __future__ import annotations
from time import perf_counter
import numpy as np
import igl


def sample_surface(points, faces, count, seed):
    areas=igl.doublearea(points,faces)*.5
    total=float(areas.sum())
    if total<=0:raise ValueError('Surface area must be positive')
    rng=np.random.default_rng(seed)
    chosen=faces[rng.choice(len(faces),count,p=areas/total)]
    uv=rng.random((count,2));mask=uv.sum(axis=1)>1;uv[mask]=1-uv[mask]
    bary=np.column_stack((1-uv.sum(axis=1),uv))
    return (points[chosen]*bary[:,:,None]).sum(axis=1),total


class SurfaceReference:
    """One immutable original, cached per-shell acceleration and fixed samples."""
    def __init__(self,points,triangles,radii,samples_per_shell=512,seed=0):
        self.points=np.asarray(points,dtype=float)
        self.triangles=np.asarray(triangles,dtype=np.int64)
        self.radii=np.asarray(radii,dtype=float)
        self.count=samples_per_shell;self.seed=seed
        if self.points.ndim!=2 or self.points.shape[1]!=3 or not np.isfinite(self.points).all():
            raise ValueError('Finite N by 3 points required')
        if self.triangles.ndim!=2 or self.triangles.shape[1]!=3 or not len(self.triangles):
            raise ValueError('Nonempty triangle array required')
        if self.triangles.min()<0 or self.triangles.max()>=len(self.points):raise ValueError('Invalid triangle indices')
        if type(self.count) is not int or self.count<32:raise ValueError('At least 32 samples per shell required')
        if self.radii.ndim!=1 or not len(self.radii) or not np.isfinite(self.radii).all() or np.any(self.radii<=0):
            raise ValueError('Positive finite distance radii required')
        number,self.labels=igl.facet_components(self.triangles)
        self.vertex_shell=np.full(len(self.points),-1,dtype=np.int64)
        low=np.full(len(self.points),np.iinfo(np.int64).max,dtype=np.int64)
        np.maximum.at(self.vertex_shell,self.triangles.ravel(),np.repeat(self.labels,3))
        np.minimum.at(low,self.triangles.ravel(),np.repeat(self.labels,3))
        if np.any((self.vertex_shell>=0)&(low!=self.vertex_shell)):
            raise ValueError('Edge-disconnected source shells share vertices')
        self.shells=[]
        for index in range(number):
            faces=self.triangles[self.labels==index]
            samples,area=sample_surface(self.points,faces,self.count,self.seed+index)
            tree=igl.AABB();tree.init(self.points,faces)
            self.shells.append({'faces':faces,'samples':samples,'area':area,'tree':tree})

    def evaluate(self,points,triangles,source_vertex_ids):
        started=perf_counter();points=np.asarray(points,dtype=float)
        triangles=np.asarray(triangles,dtype=np.int64);ids=np.asarray(source_vertex_ids,dtype=np.int64)
        if len(ids)!=len(points) or ids.min()<0 or ids.max()>=len(self.points):
            raise ValueError('Invalid candidate source correspondence')
        labels=self.vertex_shell[ids[triangles]]
        if np.any(labels!=labels[:,:1]):raise ValueError('Candidate bridges original shells')
        labels=labels[:,0];rows=[]
        for index,shell in enumerate(self.shells):
            faces=triangles[labels==index]
            rows.append(self._evaluate_shell(index,shell,points,faces))
        result=summarize(rows,self.radii)
        result.update(seconds=perf_counter()-started,source_shells=len(self.shells),
                      samples_per_shell_per_direction=self.count,
                      precision='sampled estimate; uniform area samples; source-shell restricted')
        return result

    def _evaluate_shell(self,index,shell,points,faces):
        zeros=np.zeros(len(self.radii))
        if not len(faces):
            return {'source_area':shell['area'],'candidate_area':0.,'recall':zeros,'precision':zeros,
                    'mean_forward':float('inf'),'mean_reverse':0.,'missing':True}
        samples,area=sample_surface(points,faces,self.count,self.seed+index+100000)
        tree=igl.AABB();tree.init(points,faces)
        forward=np.sqrt(np.maximum(tree.squared_distance(points,faces,shell['samples'])[0],0))
        reverse=np.sqrt(np.maximum(shell['tree'].squared_distance(self.points,shell['faces'],samples)[0],0))
        return {'source_area':shell['area'],'candidate_area':area,'missing':False,
                'recall':np.mean(forward[:,None]<=self.radii,axis=0),
                'precision':np.mean(reverse[:,None]<=self.radii,axis=0),
                'mean_forward':float(forward.mean()),'mean_reverse':float(reverse.mean())}


def summarize(rows,radii):
    original=np.array([r['source_area'] for r in rows]);candidate=np.array([r['candidate_area'] for r in rows])
    if candidate.sum()<=0:raise ValueError('Candidate has no surface')
    recalls=np.array([r['recall'] for r in rows]);precisions=np.array([r['precision'] for r in rows])
    recall=np.average(recalls,axis=0,weights=original);precision=np.average(precisions,axis=0,weights=candidate)
    score=np.divide(2*recall*precision,recall+precision,out=np.zeros_like(recall),where=recall+precision>0)
    local=np.divide(2*recalls*precisions,recalls+precisions,out=np.zeros_like(recalls),where=recalls+precisions>0)
    profiles=[{'radius':float(d),'fscore':float(np.clip(s*100,0,100)),
        'source_coverage':float(np.clip(r*100,0,100)),'candidate_coverage':float(np.clip(p*100,0,100)),
        'mean_shell_fscore':float(m*100),'worst_shell_fscore':float(w*100)}
        for d,s,r,p,m,w in zip(radii,score,recall,precision,local.mean(axis=0),local.min(axis=0))]
    return {'profiles':profiles,'missing_shells':sum(r['missing'] for r in rows),
            'source_area':float(original.sum()),'candidate_area':float(candidate.sum()),
        'mean_distance':float((np.average([r['mean_forward'] for r in rows],weights=original)+
                               np.average([r['mean_reverse'] for r in rows],weights=candidate))*.5)}
