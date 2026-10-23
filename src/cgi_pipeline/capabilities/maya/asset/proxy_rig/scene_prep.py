"""Explicit, reversible Proxy-only cleanup before the structural inventory.

The Agent chooses semantic scope; this owner validates real outgoing use.
Currently-zero animation is never treated as a constant authoring coefficient.
"""
from pathlib import Path
from time import perf_counter

from . import safety


def texture_replacements(paths, *, is_dir=Path.is_dir):
    """One directory check per texture root, including UDIM/sequence paths."""
    checked={};changes={};missing=[]
    for plug,path in paths.items():
        parts=path.replace('\\','/').split('/')
        if 'texMaster' not in parts:continue
        index=parts.index('texMaster');parts[index]='texMaster_1k'
        directory=Path('/'.join(parts[:index+1]))
        if directory not in checked:
            checked[directory]=is_dir(directory)
            if not checked[directory]:missing.append(directory.as_posix())
        if checked[directory]:changes[plug]='/'.join(parts)
    return changes,missing


def fit_skeletons(cmds):
    """Exact leaf identity, independent of hierarchy and namespaces."""
    found=cmds.ls('FitSkeleton','*:FitSkeleton',long=True) or []
    roots=sorted({_node(cmds,n) for n in found if cmds.objExists(n)
                  and n.rsplit('|',1)[-1].rsplit(':',1)[-1]=='FitSkeleton'},key=len)
    return [n for n in roots if not any(n.startswith(p+'|') for p in roots if p!=n)]


def _node(cmds, identity):
    node=safety.resolve(cmds,identity)
    if cmds.referenceQuery(node,isNodeReferenced=True):
        raise ValueError('Proxy preparation requires local sandbox nodes: '+node)
    return node


def constant_edges(cmds, root):
    from maya.api import OpenMaya as om
    nodes=[root]+(cmds.listRelatives(root,ad=True,f=True) or [])
    owned={safety.identity(cmds,n) for n in nodes};edges=[]
    for node in nodes:
        pairs=cmds.listConnections(node,s=False,d=True,p=True,c=True) or []
        for source,target in zip(pairs[::2],pairs[1::2]):
            destination=target.split('.')[0]
            if safety.identity(cmds,destination) in owned or cmds.nodeType(destination) in ('objectSet','shadingEngine','nodeGraphEditorInfo'):
                continue
            selected=om.MSelectionList();selected.add(target);target_plug=selected.getPlug(0)
            if (cmds.nodeType(destination).startswith('animCurve') and
                    om.MFnAttribute(target_plug.attribute()).dynamic and not target_plug.connectedTo(False,True)):
                # AdvancedSkeleton authoring labels (spread/middleCurl etc.)
                # use disconnect-delete attributes. They do not feed the curve
                # input/output calculation and must not be baked as rig inputs.
                continue
            if cmds.getAttr(source,type=True) not in ('bool','byte','short','long','enum','float','double'):
                raise ValueError('Construction subtree has an effective non-scalar output: '+source+' -> '+target)
            if cmds.listConnections(source,s=True,d=False) or cmds.getAttr(source,time=cmds.currentTime(q=True)) is None:
                raise ValueError('Construction subtree output is driven: '+source)
            # Alias names can disappear on disconnect; keep native plug names.
            def canonical(value):
                selected=om.MSelectionList();selected.add(value);plug=selected.getPlug(0)
                return om.MFnDependencyNode(plug.node()).name()+'.'+plug.partialName(useLongNames=True,useFullAttributePath=True,useAlias=False)
            edges.append((canonical(source),canonical(target),cmds.getAttr(target)))
    return edges


def disposable(cmds, root):
    """An upstream BS alone is not a reason to retain a dead output mesh."""
    from . import deletion_scope,finalize,outside
    shapes=cmds.listRelatives(root,s=True,f=True,type='mesh') or []
    if cmds.listRelatives(root,c=True,type='transform'):
        raise ValueError('Disposable mesh must be an explicit leaf: '+root)
    history=cmds.listHistory(root,pruneDagObjects=True) or []
    reasons=deletion_scope.inspect(cmds,root,shapes,history)['external_consumers']
    reasons+=finalize._history_consumers(cmds,root,shapes,history)
    if reasons:raise ValueError('Disposable mesh has a live consumer: '+str(reasons))
    if outside.name_users([root]+shapes,outside.script_sources(cmds)):
        raise ValueError('Disposable mesh is used by a script: '+root)


def zero_relations(cmds, nodes):
    """Resolve aliases through MPlug; remove only undriven exact-zero weights."""
    from maya.api import OpenMaya as om
    rows=[]
    for node in nodes:
        for attr in cmds.listAttr(node,userDefined=True) or []:
            if '_W_' not in attr:continue
            source=node+'.'+attr
            if cmds.getAttr(source,type=True) not in ('double','float') or cmds.listConnections(source,s=True,d=False):continue
            if cmds.getAttr(source)!=0.:continue
            targets=cmds.listConnections(source,s=False,d=True,p=True) or []
            resolved=[]
            for target in targets:
                sel=om.MSelectionList();sel.add(target);plug=sel.getPlug(0)
                owner=om.MFnDependencyNode(plug.node())
                if owner.typeName!='blendWeighted' or om.MFnAttribute(plug.attribute()).name!='weight' or not plug.isElement:
                    resolved=[];break
                index=plug.logicalIndex();name=owner.name()
                resolved.append((name+'.weight[%d]'%index,name+'.input[%d]'%index))
            if resolved:rows.append({'source':source,'ports':resolved})
    return rows


def apply_zero_relations(cmds, rows):
    candidates=set();ports=0
    for row in rows:
        for weight,value in row['ports']:
            incoming=cmds.listConnections(value,s=True,d=False,p=True) or []
            for source in incoming:
                candidates.add(source.split('.')[0]);cmds.disconnectAttr(source,value)
            cmds.disconnectAttr(row['source'],weight)
            # Native zero multiplication now has no upstream graph to evaluate.
            cmds.setAttr(value,0.);cmds.setAttr(weight,0.);ports+=1
    deleted=[]
    permitted={'pointMatrixMult','vectorProduct','multMatrix','decomposeMatrix','multiplyDivide','plusMinusAverage','unitConversion'}
    while candidates:
        node=candidates.pop()
        if not cmds.objExists(node) or cmds.nodeType(node) not in permitted:continue
        outgoing=cmds.listConnections(node,s=False,d=True,p=True) or []
        if any(cmds.nodeType(p.split('.')[0]) not in ('nodeGraphEditorInfo',) for p in outgoing):continue
        candidates.update(cmds.listConnections(node,s=True,d=False) or [])
        cmds.delete(node);deleted.append(node)
    return {'constant_zero_relations':len(rows),'disconnected_weighted_inputs':ports,'deleted_exclusive_nodes':deleted}


def run(payload):
    import maya.cmds as cmds
    options=dict(payload.get('parameters',{}).get('preparation') or {})
    allowed={'fit_skeleton','constant_subtrees','discard_meshes','visibility_values','textures_1k','zero_weight_nodes'}
    if set(options)-allowed:raise ValueError('Unknown preparation option')
    safety.managed(cmds.file(q=True,sn=True),suffix={'.ma','.mb'})
    started=perf_counter();roots=[_node(cmds,x) for x in options.get('constant_subtrees',[])]
    fit=fit_skeletons(cmds) if options.get('fit_skeleton') else []
    roots=[n for n in roots if not any(n==f or n.startswith(f+'|') for f in fit)]
    dead=[_node(cmds,x) for x in options.get('discard_meshes',[])]
    edges={root:constant_edges(cmds,root) for root in roots}
    for node in dead:disposable(cmds,node)
    visibility=options.get('visibility_values',{})
    for plug,value in visibility.items():
        if type(value) not in (bool,int,float) or not cmds.objExists(plug) or not cmds.getAttr(plug,settable=True) or cmds.listConnections(plug,s=True,d=False):
            raise ValueError('Visibility driver must be explicit, numeric and undriven: '+plug)
    textures={};missing=[]
    if options.get('textures_1k'):
        paths={node+'.fileTextureName':cmds.getAttr(node+'.fileTextureName') or '' for node in cmds.ls(type='file') or []}
        textures,missing=texture_replacements(paths)
    zeros=zero_relations(cmds,[_node(cmds,x) for x in options.get('zero_weight_nodes',[])])
    report={'preflight_seconds':perf_counter()-started,'removed_subtrees':[],
            'removed_meshes':dead,'removed_fit_skeletons':fit,'textures_replaced':len(textures),'textures_unavailable':missing,'visibility':visibility}
    chunk='ProxyScenePreparation';cmds.undoInfo(openChunk=True,chunkName=chunk)
    try:
        marker=cmds.createNode('network',name='cgi_proxy_preparation_transaction')
        if fit:cmds.delete(fit)
        for root,connections in edges.items():
            for source,target,value in connections:
                locked=cmds.getAttr(target,lock=True)
                if locked:cmds.setAttr(target,lock=False)
                cmds.disconnectAttr(source,target);cmds.setAttr(target,value)
                if locked:cmds.setAttr(target,lock=True)
            cmds.delete(root)
            preserved=0;consumed=[]
            for _,target,value in connections:
                if not cmds.objExists(target):
                    consumed.append(target);continue
                if cmds.getAttr(target)!=value:raise RuntimeError('Construction constant changed: '+target)
                preserved+=1
            report['removed_subtrees'].append({'path':root,'preserved_constants':preserved,'removed_construction_targets':consumed})
        if dead:cmds.delete(dead)
        for plug,value in visibility.items():cmds.setAttr(plug,value)
        for plug,path in textures.items():cmds.setAttr(plug,path,type='string')
        t=perf_counter();report['zero_weights']=apply_zero_relations(cmds,zeros)
        report['zero_weight_seconds']=perf_counter()-t
        cmds.delete(marker)
    except Exception:
        cmds.undoInfo(closeChunk=True);cmds.undo();raise
    else:cmds.undoInfo(closeChunk=True)
    report['seconds']=perf_counter()-started
    return 'SUCCESS',report
