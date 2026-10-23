"""Source-only geometry units and bounded-variation Reduce work groups."""
from __future__ import annotations
from time import perf_counter
import numpy as np


def cut_budget_ok(triangles, polygon_ids, pairs, sections, keep_ratio):
    """Do not spend a large part of the reduced mesh on artificial cut seams."""
    membership={int(face):index for index,section in enumerate(sections) for face in section}
    seam=set()
    for a,b in pairs:
        if membership[int(polygon_ids[a])]!=membership[int(polygon_ids[b])]:
            seam.update(set(triangles[a]).intersection(triangles[b]))
    source_vertices=len(np.unique(triangles[np.unique(pairs)])) if len(pairs) else 0
    return len(seam)<=.25*keep_ratio*source_vertices


def plan(points,triangles,polygon_ids,minimal_faces=12,boundary_features=None,target_keep_ratio=.1,partition_continuous=True):
    import igl
    from sklearn.cluster import AgglomerativeClustering,KMeans
    from threadpoolctl import threadpool_limits
    started=perf_counter();v=np.asarray(points,dtype=float);f=np.asarray(triangles,dtype=np.int64)
    p=np.asarray(polygon_ids,dtype=np.int64);number,labels=igl.facet_components(f)
    area=igl.doublearea(v,f)*.5;normals=igl.per_face_normals(v,f,np.array([0.,0.,1.]))
    adjacency=igl.triangle_triangle_adjacency(f)[0];units=[]
    for shell in range(number):
        ids=np.flatnonzero(labels==shell);polygons=np.unique(p[ids]);vertices=v[np.unique(f[ids])]
        pairs=np.array([(i,int(j)) for i in ids for j in adjacency[i] if j>i],dtype=np.int64).reshape(-1,2)
        angles=np.arccos(np.clip((normals[pairs[:,0]]*normals[pairs[:,1]]).sum(axis=1),-1,1)) if len(pairs) else np.zeros(1)
        eigen=np.maximum(np.linalg.eigvalsh(np.cov(vertices.T)),1e-12)
        surface=float(area[ids].sum());aspect=float(np.sqrt(eigen[-1]/eigen[-2]))
        if surface<=0:raise ValueError('Degenerate source shell')
        # Large within-shell density changes become face regions before Reduce.
        # Whole shells stay intact otherwise. Split cuts are protected later.
        face_areas=np.bincount(p[ids],weights=area[ids])[polygons]
        spread=np.quantile(face_areas,.9)/max(np.quantile(face_areas,.1),1e-12)
        if partition_continuous and len(polygons)>=2048 and spread>8:
            with threadpool_limits(limits=1):
                assignment=KMeans(n_clusters=2,random_state=0,n_init=10).fit_predict(np.log(np.maximum(face_areas,1e-12))[:,None])
            sections=[polygons[assignment==k] for k in range(2)]
            # Area clusters can alternate over one continuous surface. Their
            # protected cuts then dominate the target budget and create fans.
            # Keep that shell intact; coherent low-cost density regions still split.
            if not cut_budget_ok(f,p,pairs,sections,target_keep_ratio):sections=[polygons]
        else:sections=[polygons]
        for section in sections:
            selected=ids[np.isin(p[ids],section)];local_area=float(area[selected].sum())
            feature=[np.log2(local_area)/2.,np.log2(aspect),np.degrees(np.quantile(angles,.9))/10.,
                     np.log2(len(section)/local_area)/2.]
            if boundary_features is not None:
                # Native feature protection competes for the same group budget.
                # Separate materially different constraints before any reduction.
                feature.extend((np.mean(np.asarray(boundary_features)[section],axis=0)/.125).tolist())
            units.append({'faces':section,'shell':shell,'feature':feature,'area':local_area,
                          'protected_cut':len(sections)>1,'preserve':len(section)<=minimal_faces})
    groups=[]
    for cut in (False,True):
        active=[u for u in units if not u['preserve'] and u['protected_cut']==cut]
        if not active:continue
        features=np.asarray([u['feature'] for u in active])
        with threadpool_limits(limits=1):
            assignment=(AgglomerativeClustering(n_clusters=None,distance_threshold=1.,linkage='complete',metric='chebyshev').fit_predict(features)
                        if len(active)>1 else np.zeros(1,dtype=int))
        for label in np.unique(assignment):
            members=[u for u,tag in zip(active,assignment) if tag==label]
            groups.append({'faces':sorted(int(x) for u in members for x in u['faces']),
                           'units':len(members),'preserve':False,'preserve_border':cut,
                           'surface_area':sum(u['area'] for u in members)})
    kept=[u for u in units if u['preserve']]
    if kept:groups.append({'faces':sorted(int(x) for u in kept for x in u['faces']),
                           'units':len(kept),'preserve':True,'preserve_border':True,'surface_area':sum(u['area'] for u in kept)})
    groups.sort(key=lambda g:(g['preserve'],g['faces'][0]))
    for i,group in enumerate(groups):group['name']='geometry_'+str(i)
    covered=[x for g in groups for x in g['faces']]
    if len(covered)!=len(set(covered)) or set(covered)!=set(p):raise ValueError('Geometry grouping is not an exhaustive partition')
    return {'groups':groups,'source_shells':number,'analysis_units':len(units),'seconds':perf_counter()-started,
            'method':'source area, PCA aspect, adjacent-normal bend, face density; complete-link bounded variation',
            'continuous_scope':('two source face-area classes when p90/p10 > 8 and cut vertices <= 25% of target vertex budget; source-identity stitching after reduction'
                                if partition_continuous else 'whole connected shells; Agent supplies functional face partitions explicitly')}
