"""
Spatial mapping benchmark: trouser_001_msh -> maYouB_clothes3
Tests 3 strategies: 1-NN, K8-IDW, TNB projection
"""
import maya.cmds as cmds
import numpy as np
from maya.api import OpenMaya as om2
from maya.api import OpenMayaAnim as oma2
from scipy.spatial import cKDTree
import time, json, traceback


def get_verts(path):
    sel = om2.MSelectionList()
    sel.add(path)
    dag = sel.getDagPath(0)
    if dag.node().apiType() != om2.MFn.kMesh:
        dag.extendToShape()
    fn = om2.MFnMesh(dag)
    pts = fn.getPoints(om2.MSpace.kWorld)
    return np.array([[p.x, p.y, p.z] for p in pts])


def get_tris(path):
    sel = om2.MSelectionList()
    sel.add(path)
    dag = sel.getDagPath(0)
    if dag.node().apiType() != om2.MFn.kMesh:
        dag.extendToShape()
    fn = om2.MFnMesh(dag)
    _, tri_verts = fn.getTriangles()
    return np.array(tri_verts).reshape(-1, 3)


def get_normals(path):
    sel = om2.MSelectionList()
    sel.add(path)
    dag = sel.getDagPath(0)
    if dag.node().apiType() != om2.MFn.kMesh:
        dag.extendToShape()
    fn = om2.MFnMesh(dag)
    norms = fn.getVertexNormals(False, om2.MSpace.kWorld)
    return np.array([[n.x, n.y, n.z] for n in norms])


def get_skin(path):
    shapes = cmds.listRelatives(path, shapes=True, noIntermediate=True, fullPath=True) or []
    skin_node = None
    for s in shapes:
        for h in (cmds.listHistory(s, pruneDagObjects=True) or []):
            if cmds.nodeType(h) == "skinCluster":
                skin_node = h
                break
        if skin_node:
            break
    if not skin_node:
        return None, None
    joints = cmds.skinCluster(skin_node, query=True, influence=True)
    sel = om2.MSelectionList()
    sel.add(skin_node)
    fn_skin = oma2.MFnSkinCluster(sel.getDependNode(0))
    sel2 = om2.MSelectionList()
    sel2.add(shapes[0])
    dag_shape = sel2.getDagPath(0)
    nv = om2.MFnMesh(dag_shape).numVertices
    comp_fn = om2.MFnSingleIndexedComponent()
    comp = comp_fn.create(om2.MFn.kMeshVertComponent)
    comp_fn.setCompleteData(nv)
    weights, _ = fn_skin.getWeights(dag_shape, comp)
    return np.array(weights).reshape(nv, len(joints)), joints


def edge_smooth(faces, weights, nv, sample=5000):
    edges = set()
    for f in faces:
        for i in range(3):
            a, b = int(f[i]), int(f[(i + 1) % 3])
            if a < nv and b < nv:
                edges.add((min(a, b), max(a, b)))
    edge_list = list(edges)[:sample]
    diffs = np.array([np.linalg.norm(weights[a] - weights[b]) for a, b in edge_list])
    return float(np.percentile(diffs, 95)), float(np.mean(diffs))


def run_benchmark():
    geo_path = "|MaYou_B|geo|cst|trouser_grp|trouser_001_msh"
    ref = "ysj_chr_maYouB_tex_texMaster_v001:"
    cache_path = "|{0}Group|{0}cache|{0}maYouB_cloth_Grp|{0}maYouB_clothes_Grp|{0}maYouB_clothes3".format(ref)

    src_v = get_verts(geo_path)
    dst_v = get_verts(cache_path)
    src_n = get_normals(geo_path)
    dst_n = get_normals(cache_path)
    src_f = get_tris(geo_path)
    dst_f = get_tris(cache_path)
    src_w, src_joints = get_skin(geo_path)

    N_src, N_dst, N_j = len(src_v), len(dst_v), src_w.shape[1]

    # === Strategy 1: 1-NN ===
    t0 = time.time()
    tree = cKDTree(src_v)
    nn_d, nn_i = tree.query(dst_v, k=1)
    w_nn = src_w[nn_i]
    t_nn = time.time() - t0

    # === Strategy 2: K=8 Gaussian IDW ===
    t0 = time.time()
    kd, ki = tree.query(dst_v, k=8)
    sigma = max(float(np.median(kd[:, -1])), 0.01)
    gauss = np.exp(-(kd ** 2) / (2 * sigma ** 2))
    gs = gauss.sum(axis=1, keepdims=True)
    gs[gs == 0] = 1.0
    gauss /= gs
    w_idw = np.zeros((N_dst, N_j))
    for k in range(8):
        w_idw += gauss[:, k:k + 1] * src_w[ki[:, k]]
    rs = w_idw.sum(axis=1, keepdims=True)
    rs[rs == 0] = 1.0
    w_idw /= rs
    t_idw = time.time() - t0

    # === Strategy 3: TNB Projection ===
    t0 = time.time()
    v0 = src_v[src_f[:, 0]]
    v1 = src_v[src_f[:, 1]]
    v2 = src_v[src_f[:, 2]]
    face_n = np.cross(v1 - v0, v2 - v0)
    fn_len = np.linalg.norm(face_n, axis=1, keepdims=True)
    fn_len[fn_len == 0] = 1.0
    face_n /= fn_len
    centroids = (v0 + v1 + v2) / 3.0
    tree_c = cKDTree(centroids)
    k_cand = 16
    _, cand_i = tree_c.query(dst_v, k=k_cand)

    w_tnb = np.zeros((N_dst, N_j))
    tnb_hit = 0

    for qi in range(N_dst):
        qp = dst_v[qi]
        qn = dst_n[qi]
        qn_len = np.linalg.norm(qn)
        if qn_len < 1e-10:
            w_tnb[qi] = w_nn[qi]
            continue
        qn = qn / qn_len
        found = False
        for ci in range(k_cand):
            fi = cand_i[qi, ci]
            if np.dot(qn, face_n[fi]) < 0:
                continue
            A = src_v[src_f[fi, 0]]
            B = src_v[src_f[fi, 1]]
            C = src_v[src_f[fi, 2]]
            ab = B - A
            ac = C - A
            ap = qp - A
            d00 = np.dot(ab, ab)
            d01 = np.dot(ab, ac)
            d11 = np.dot(ac, ac)
            d20 = np.dot(ap, ab)
            d21 = np.dot(ap, ac)
            denom = d00 * d11 - d01 * d01
            if abs(denom) < 1e-12:
                continue
            bv = (d11 * d20 - d01 * d21) / denom
            bw = (d00 * d21 - d01 * d20) / denom
            bu = 1.0 - bv - bw
            if bu >= -0.1 and bv >= -0.1 and bw >= -0.1:
                bu = max(bu, 0)
                bv = max(bv, 0)
                bw = max(bw, 0)
                s = bu + bv + bw
                if s > 0:
                    bu /= s
                    bv /= s
                    bw /= s
                w_tnb[qi] = (
                    bu * src_w[src_f[fi, 0]]
                    + bv * src_w[src_f[fi, 1]]
                    + bw * src_w[src_f[fi, 2]]
                )
                found = True
                tnb_hit += 1
                break
        if not found:
            w_tnb[qi] = w_nn[qi]

    rs = w_tnb.sum(axis=1, keepdims=True)
    rs[rs == 0] = 1.0
    w_tnb /= rs
    t_tnb = time.time() - t0

    # === Metrics ===
    es_nn_p95, es_nn_mean = edge_smooth(dst_f, w_nn, N_dst)
    es_idw_p95, es_idw_mean = edge_smooth(dst_f, w_idw, N_dst)
    es_tnb_p95, es_tnb_mean = edge_smooth(dst_f, w_tnb, N_dst)

    sp_src = float(np.median(np.sum(src_w > 0.01, axis=1)))
    sp_nn = float(np.median(np.sum(w_nn > 0.01, axis=1)))
    sp_idw = float(np.median(np.sum(w_idw > 0.01, axis=1)))
    sp_tnb = float(np.median(np.sum(w_tnb > 0.01, axis=1)))

    # Weight difference between strategies
    diff_nn_tnb = float(np.mean(np.abs(w_nn - w_tnb).sum(axis=1)))
    diff_idw_tnb = float(np.mean(np.abs(w_idw - w_tnb).sum(axis=1)))

    out = {
        "mesh": "trouser_001_msh(4301) -> maYouB_clothes3(4301)",
        "joints": N_j,
        "joint_names": src_joints,
        "source_sparsity": sp_src,
        "strategies": {
            "1NN": {
                "time_ms": round(t_nn * 1000, 1),
                "edge_smooth_p95": round(es_nn_p95, 5),
                "edge_smooth_mean": round(es_nn_mean, 5),
                "sparsity": sp_nn,
                "max_dist": round(float(nn_d.max()), 4),
                "mean_dist": round(float(nn_d.mean()), 4),
            },
            "K8_IDW": {
                "time_ms": round(t_idw * 1000, 1),
                "edge_smooth_p95": round(es_idw_p95, 5),
                "edge_smooth_mean": round(es_idw_mean, 5),
                "sparsity": sp_idw,
                "sigma": round(sigma, 4),
            },
            "TNB": {
                "time_ms": round(t_tnb * 1000, 1),
                "edge_smooth_p95": round(es_tnb_p95, 5),
                "edge_smooth_mean": round(es_tnb_mean, 5),
                "sparsity": sp_tnb,
                "hit_rate": round(tnb_hit / N_dst * 100, 1),
            },
        },
        "cross_strategy_diff": {
            "1NN_vs_TNB_mean_L1": round(diff_nn_tnb, 5),
            "IDW_vs_TNB_mean_L1": round(diff_idw_tnb, 5),
        },
    }

    with open(r"Y:\GGbommer\scripts\CGI_Pipeline\bench_result.json", "w") as f:
        json.dump(out, f, indent=2)

    return out


try:
    run_benchmark()
except Exception:
    with open(r"Y:\GGbommer\scripts\CGI_Pipeline\bench_error.txt", "w") as f:
        f.write(traceback.format_exc())
