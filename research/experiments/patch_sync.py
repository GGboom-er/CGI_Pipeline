import re
import os

filepath = r"Y:\GGbommer\scripts\CGI_Pipeline\skills\maya_sync_rig_incremental\maya_sync_rig_incremental.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add _build_adjacency and _reorder_weight_transfer functions
func_str = """
def _build_adjacency(num_verts, face_indices, face_counts):
    adjacency = [set() for _ in range(num_verts)]
    idx = 0
    for count in face_counts:
        face_verts = face_indices[idx:idx+count]
        for i in range(count):
            v1 = face_verts[i]
            v2 = face_verts[(i+1)%count]
            adjacency[v1].add(v2)
            adjacency[v2].add(v1)
        idx += count
    return [list(s) for s in adjacency]

def _reorder_weight_transfer(new_mesh, num_new_verts, tex_data,
                             paired_rig_dag, rig_meshes, rig_skin_data, rig_bs_data,
                             target_name, items, sync_nodes, new_nodes):
    import numpy as np
    mapping = tex_data.get("_reorder_mapping")
    if not mapping: return False
    sk_data = rig_skin_data.get(paired_rig_dag)
    if not sk_data: return False
    
    src_joints = sk_data["joints"]
    src_weights = sk_data["weights"]
    num_src_verts = src_weights.shape[0]
    
    if len(mapping) != num_new_verts: return False
    
    new_weights = np.zeros((num_new_verts, len(src_joints)), dtype=np.float64)
    for i, old_idx in enumerate(mapping):
        if 0 <= old_idx < num_src_verts:
            new_weights[i] = src_weights[old_idx]
            
    row_sums = new_weights.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    new_weights = new_weights / row_sums
    
    active_cols = np.where(new_weights.max(axis=0) > 0.0)[0]
    if len(active_cols) == 0: return False
    
    bind_joints = [src_joints[c] for c in active_cols]
    existing_joints = [j for j in bind_joints if cmds.objExists(j)]
    if not existing_joints: return False
    if len(existing_joints) < len(bind_joints):
        exist_mask = np.array([cmds.objExists(src_joints[c]) for c in active_cols])
        active_cols = active_cols[exist_mask]
        bind_joints = [src_joints[c] for c in active_cols]
        
    compact_w = new_weights[:, active_cols]
    
    try:
        new_skin = cmds.skinCluster(
            new_mesh, bind_joints, toSelectedBones=True,
            bindMethod=0, skinMethod=0, normalizeWeights=1,
            name=target_name + "_skinCluster")[0]

        sel_ns = om2.MSelectionList()
        sel_ns.add(new_skin)
        fn_ns = oma2.MFnSkinCluster(sel_ns.getDependNode(0))

        new_shapes = cmds.listRelatives(new_mesh, shapes=True, fullPath=True, noIntermediate=True)
        sel_nsh = om2.MSelectionList()
        sel_nsh.add(new_shapes[0])
        nd = sel_nsh.getDagPath(0)

        nnv = om2.MFnMesh(nd).numVertices
        nc = om2.MFnSingleIndexedComponent().create(om2.MFn.kMeshVertComponent)
        om2.MFnSingleIndexedComponent(nc).setCompleteData(nnv)

        inf_idx = om2.MIntArray(list(range(len(bind_joints))))
        fn_ns.setWeights(nd, nc, inf_idx, om2.MDoubleArray(compact_w.flatten().tolist()))

        sync_nodes.append(new_mesh)
        items.append(make_item(target_name, f"REORDER: 按映射表同步 ({num_new_verts} verts)"))
        
        # Simple BlendShape Reorder
        if paired_rig_dag in rig_bs_data:
            bs_info_list = rig_bs_data[paired_rig_dag]
            bs_count = 0
            for bs_info in bs_info_list:
                if not bs_info["targets"]: continue
                old_bs_name = bs_info.get("bs_node", "blendShape")
                new_bs_suffix = old_bs_name.replace(RIG_PREFIX, "")
                try:
                    new_bs = cmds.blendShape(new_mesh, name=f"{target_name}_{new_bs_suffix}", frontOfChain=True)[0]
                    sel_bs = om2.MSelectionList(); sel_bs.add(new_bs)
                    fn_bs = om2.MFnDependencyNode(sel_bs.getDependNode(0))
                    it_plug = fn_bs.findPlug("inputTarget", False)
                    geom_idx = it_plug.getExistingArrayAttributeIndices()[0]
                    itg_plug = it_plug.elementByLogicalIndex(geom_idx).child(0)
                    
                    for tgt in bs_info["targets"]:
                        t_idx = tgt["index"]
                        cmds.aliasAttr(tgt["name"], f"{new_bs}.weight[{t_idx}]")
                        tgt_plug = itg_plug.elementByLogicalIndex(t_idx)
                        iti_array_plug = tgt_plug.child(0)
                        
                        for item_data in tgt["items"]:
                            item_idx = item_data["item_index"]
                            old_delta = item_data["delta"]
                            new_delta = np.zeros((num_new_verts, 3), dtype=np.float64)
                            for i, old_idx in enumerate(mapping):
                                if 0 <= old_idx < len(old_delta):
                                    new_delta[i] = old_delta[old_idx]
                                    
                            norms_d = np.linalg.norm(new_delta, axis=1)
                            sparse_ids_arr = np.where(norms_d > 0.00001)[0]
                            if len(sparse_ids_arr) == 0: continue
                            
                            sparse_pts = om2.MPointArray()
                            for si in sparse_ids_arr:
                                d = new_delta[si]
                                sparse_pts.append(om2.MPoint(d[0], d[1], d[2]))
                                
                            iti_plug_d = iti_array_plug.elementByLogicalIndex(item_idx)
                            for ci in range(iti_plug_d.numChildren()):
                                child = iti_plug_d.child(ci)
                                attr_name = om2.MFnAttribute(child.attribute()).name
                                if attr_name == "inputPointsTarget": ipt_plug = child
                                elif attr_name == "inputComponentsTarget": ict_plug = child
                                
                            ipt_plug.setMObject(om2.MFnPointArrayData().create(sparse_pts))
                            fn_comp = om2.MFnSingleIndexedComponent()
                            comp_obj = fn_comp.create(om2.MFn.kMeshVertComponent)
                            fn_comp.addElements(sparse_ids_arr.tolist())
                            fn_comp_data = om2.MFnComponentListData()
                            comp_list_obj = fn_comp_data.create()
                            fn_comp_data.add(comp_obj)
                            ict_plug.setMObject(comp_list_obj)
                        bs_count += 1
                        try: cmds.setAttr(f"{new_bs}.{tgt['name']}", tgt["weight_value"])
                        except: pass
                except: pass
            if bs_count > 0:
                items.append(make_item(target_name, f"  BS: {bs_count} targets reordered"))
        return True
    except Exception as e:
        return False
"""

# Replace _directed_weight_transfer definition and parameters
content = content.replace("def _directed_weight_transfer(new_mesh, new_verts, num_new_verts, new_normals,",
                          func_str + "\ndef _directed_weight_transfer(new_mesh, new_verts, num_new_verts, new_normals, tex_data,")

# Add Laplacian diffusion to _directed_weight_transfer
lap_code = """
    # 权重插值
    new_weights = np.zeros((num_new_verts, len(src_joints)), dtype=np.float64)
    valid_mask = dists <= 0.01  # 精确映射阈值

    valid_idx = np.where(valid_mask)[0]
    if len(valid_idx) == 0:
        return False

    lv0 = fvi[valid_idx, 0]
    lv1 = fvi[valid_idx, 1]
    lv2 = fvi[valid_idx, 2]

    bounds_ok = (lv0 >= 0) & (lv1 >= 0) & (lv2 >= 0) & \\
                (lv0 < num_src_verts) & (lv1 < num_src_verts) & (lv2 < num_src_verts)
    valid_idx = valid_idx[bounds_ok]
    lv0 = lv0[bounds_ok]
    lv1 = lv1[bounds_ok]
    lv2 = lv2[bounds_ok]

    if len(valid_idx) == 0:
        return False

    w0 = src_weights[lv0]
    w1 = src_weights[lv1]
    w2 = src_weights[lv2]
    b = bary[valid_idx]
    interp = b[:, 0:1] * w0 + b[:, 1:2] * w1 + b[:, 2:3] * w2
    new_weights[valid_idx] = interp
    
    # Laplacian 扩散
    if len(valid_idx) < num_new_verts:
        from core.laplacian_diffuse import laplacian_diffuse
        adjacency = _build_adjacency(num_new_verts, tex_data.get("face_indices", []), tex_data.get("face_counts", []))
        new_weights = laplacian_diffuse(adjacency, valid_idx, new_weights[valid_idx], num_new_verts, len(src_joints))
"""

old_weight_code = """    # 权重插值
    new_weights = np.zeros((num_new_verts, len(src_joints)), dtype=np.float64)
    valid_mask = dists <= 10.0  # 定向投射允许更大距离（同一物体拓扑变化）

    valid_idx = np.where(valid_mask)[0]
    if len(valid_idx) == 0:
        return False

    lv0 = fvi[valid_idx, 0]
    lv1 = fvi[valid_idx, 1]
    lv2 = fvi[valid_idx, 2]

    bounds_ok = (lv0 >= 0) & (lv1 >= 0) & (lv2 >= 0) & \\
                (lv0 < num_src_verts) & (lv1 < num_src_verts) & (lv2 < num_src_verts)
    valid_idx = valid_idx[bounds_ok]
    lv0 = lv0[bounds_ok]
    lv1 = lv1[bounds_ok]
    lv2 = lv2[bounds_ok]

    if len(valid_idx) == 0:
        return False

    w0 = src_weights[lv0]
    w1 = src_weights[lv1]
    w2 = src_weights[lv2]
    b = bary[valid_idx]
    interp = b[:, 0:1] * w0 + b[:, 1:2] * w1 + b[:, 2:3] * w2
    new_weights[valid_idx] = interp"""

content = content.replace(old_weight_code, lap_code)

# Fix execute parameter passing for _directed_weight_transfer
execute_replace = """
            if paired_rig_dag and paired_rig_dag in rig_skin_data and paired_rig_dag in rig_meshes:
                if tex_data.get("_reorder_mapping"):
                    _ok = _reorder_weight_transfer(
                        new_mesh, num_new_verts, tex_data,
                        paired_rig_dag, rig_meshes, rig_skin_data, rig_bs_data,
                        target_name, items, sync_nodes, new_nodes
                    )
                    if _ok: continue
                else:
                    _ok = _directed_weight_transfer(
                        new_mesh, new_verts, num_new_verts, new_normals, tex_data,
                        paired_rig_dag, rig_meshes, rig_skin_data, rig_bs_data,
                        target_name, items, sync_nodes, new_nodes
                    )
                    if _ok: continue
"""

old_execute_code = """
            if paired_rig_dag and paired_rig_dag in rig_skin_data and paired_rig_dag in rig_meshes:
                _ok = _directed_weight_transfer(
                    new_mesh, new_verts, num_new_verts, new_normals,
                    paired_rig_dag, rig_meshes, rig_skin_data, rig_bs_data,
                    target_name, items, sync_nodes, new_nodes
                )
                if _ok:
                    continue
"""
content = content.replace(old_execute_code, execute_replace)

# Make sure SPATIAL_VOTING keeps _reorder_mapping
content = content.replace('tex_data["_paired_rig_dag"] = action_data["rig_dag"]',
                          'tex_data["_paired_rig_dag"] = action_data["rig_dag"]\n                    if action_data.get("reorder_mapping"):\n                        tex_data["_reorder_mapping"] = action_data.get("reorder_mapping")')

# Also MERGE / SPLIT support:
merge_split_code = """
            elif action in ("SPATIAL_VOTING", "REVIEW", "NEW", "MERGE", "SPLIT", "REORDER"):
"""
content = content.replace('elif action in ("SPATIAL_VOTING", "REVIEW", "NEW"):', merge_split_code)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)
print("Patch applied.")
