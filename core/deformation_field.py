"""
体积化形变场引擎 — 在 Celery Worker 中运行，不依赖 Maya。

核心流程：
  1. 从 NPZ 加载所有 rig mesh 的 (V, F, N, W, BS)
  2. 合并为 SuperMesh → 预计算 Winding Number 八叉树 + BVH
  3. 对任意新 mesh 采样：
     a. Winding Number 层级判定
     b. 法线一致射线投射 → 从同层面重心插值
     c. 未命中顶点 → 热扩散在新 mesh 自身拓扑上补全
     d. Laplacian 平滑 + 归一化

依赖：numpy, scipy, igl (libigl), trimesh, potpourri3d
"""

import numpy as np
import time
import logging

logger = logging.getLogger(__name__)


class DeformationField:
    """体积化形变场：从多个 rig mesh 构建连续空间权重/BS 场。"""

    def __init__(self, rig_data):
        """
        Parameters
        ----------
        rig_data : dict — load_rig_data() 的返回值
            包含 'all_joints' 和 'meshes' 列表
        """
        self.all_joints = rig_data['all_joints']
        self.num_joints = len(self.all_joints)
        self.meshes = rig_data['meshes']

        # 构建阶段
        self._super_verts = None     # (N_total, 3)
        self._super_faces = None     # (M_total, 3)
        self._super_weights = None   # (N_total, J)
        self._face_source_id = None  # (M_total,) — 每个面属于哪个源 mesh
        self._vert_offset = []       # 每个 mesh 的顶点偏移

        self._trimesh = None         # trimesh.Trimesh 对象

        self._build_super_mesh()

    # ═══════════════════════════════════════════════════════════
    # Phase 1: 场构建
    # ═══════════════════════════════════════════════════════════

    def _build_super_mesh(self):
        """合并所有 rig mesh 为 SuperMesh，保留来源标记。"""
        t0 = time.time()

        all_verts = []
        all_faces = []
        all_weights = []
        face_source = []
        vert_offset = 0

        for mesh_idx, mesh in enumerate(self.meshes):
            V = np.asarray(mesh['vertices'], dtype=np.float64)
            F = np.asarray(mesh['faces'], dtype=np.intp)
            N_v = V.shape[0]

            all_verts.append(V)
            all_faces.append(F + vert_offset)

            # 权重：如果有就用，没有就填零
            W = mesh.get('weights')
            if W is not None:
                W = np.asarray(W, dtype=np.float64)
                # 确保列数一致
                if W.shape[1] < self.num_joints:
                    pad = np.zeros((N_v, self.num_joints - W.shape[1]))
                    W = np.hstack([W, pad])
                elif W.shape[1] > self.num_joints:
                    W = W[:, :self.num_joints]
            else:
                W = np.zeros((N_v, self.num_joints), dtype=np.float64)

            all_weights.append(W)
            face_source.append(np.full(F.shape[0], mesh_idx, dtype=np.intp))

            self._vert_offset.append(vert_offset)
            vert_offset += N_v

        self._super_verts = np.vstack(all_verts)
        self._super_faces = np.vstack(all_faces)
        self._super_weights = np.vstack(all_weights)
        self._face_source_id = np.concatenate(face_source)

        logger.info(
            f"SuperMesh built: {self._super_verts.shape[0]} verts, "
            f"{self._super_faces.shape[0]} faces, "
            f"{len(self.meshes)} sources — {time.time()-t0:.2f}s"
        )

    def _ensure_trimesh(self):
        """懒加载 trimesh 对象。"""
        if self._trimesh is None:
            import trimesh
            self._trimesh = trimesh.Trimesh(
                vertices=self._super_verts,
                faces=self._super_faces,
                process=False
            )

    # ═══════════════════════════════════════════════════════════
    # Phase 2: 采样 — 核心 API
    # ═══════════════════════════════════════════════════════════

    def sample_weights(self, new_verts, new_faces, new_normals=None,
                       use_winding=True, max_normal_angle_deg=90.0,
                       diffuse_backend='heat', smooth_iterations=0,
                       idw_k=8, idw_blend=0.3):
        """
        混合权重采样：重心投射 + K近邻 IDW 混合。

        Parameters
        ----------
        new_verts : ndarray (N, 3) — 新 mesh 顶点
        new_faces : ndarray (M, 3) — 新 mesh 三角面
        new_normals : ndarray (N, 3) optional — 顶点法线
        use_winding : bool — 是否使用 Winding Number 层级筛选
        max_normal_angle_deg : float — 法线过滤角度阈值
        diffuse_backend : str — 'heat' | 'scipy'
        smooth_iterations : int — Laplacian 平滑轮数
        idw_k : int — IDW 近邻数 (0=禁用)
        idw_blend : float — IDW 混合比例 (0.0=纯重心, 1.0=纯IDW)

        Returns
        -------
        weights : ndarray (N, J) — 归一化权重矩阵
        stats : dict — 统计信息
        """
        t0 = time.time()
        N = new_verts.shape[0]

        if new_normals is None:
            new_normals = _compute_vertex_normals(new_verts, new_faces)

        # Step 1: Winding Number 层级判定（多源时启用）
        if use_winding and len(self.meshes) > 1:
            target_layers = self._classify_layer(new_verts)
        else:
            target_layers = None

        # Step 2: 三轮级联投射 → 重心插值
        bary_weights, valid_mask = self._ray_cast_with_layer_filter(
            new_verts, new_normals, target_layers, max_normal_angle_deg
        )

        n_hit = int(valid_mask.sum())
        n_miss = N - n_hit
        logger.info(f"Ray cast: {n_hit}/{N} hit, {n_miss} fallback")

        # Step 3: IDW 混合（跨面边界平滑）
        if idw_k > 0 and idw_blend > 0:
            idw_weights = self._idw_sample(new_verts, idw_k, target_layers)
            # 自适应混合：投射距离越近 → 越信重心；越远 → 越信 IDW
            weights = (1.0 - idw_blend) * bary_weights + idw_blend * idw_weights
        else:
            weights = bary_weights

        # Step 4: 可选 Laplacian 平滑
        if smooth_iterations > 0:
            weights = self._laplacian_smooth(
                new_verts, new_faces, weights, smooth_iterations
            )

        weights = _normalize_weights(weights)

        stats = {
            'n_total': N,
            'n_ray_hit': n_hit,
            'n_diffused': n_miss,
            'n_joints': self.num_joints,
            'time': time.time() - t0,
        }
        logger.info(f"sample_weights done: {stats}")
        return weights, stats

    def sample_bs_deltas(self, new_verts, new_faces, new_normals=None,
                         use_winding=True, max_normal_angle_deg=90.0,
                         diffuse_backend='heat', idw_k=8, idw_blend=0.3,
                         use_deformation_gradient=True):
        """
        BS delta 采样：对每个 target 的 X/Y/Z 分量分别扩散。
        启用 use_deformation_gradient 时，使用类似 Wrap4D 的局部坐标系形变梯度传递。

        Returns
        -------
        dict : {target_name: ndarray (N, 3)}
        """
        N = new_verts.shape[0]
        if new_normals is None:
            new_normals = _compute_vertex_normals(new_verts, new_faces)

        # 收集所有源 mesh 的 BS delta
        all_bs = {}
        for mesh_idx, mesh in enumerate(self.meshes):
            bs = mesh.get('bs_deltas', {})
            for name, delta in bs.items():
                if name not in all_bs:
                    all_bs[name] = []
                all_bs[name].append((mesh_idx, np.asarray(delta)))

        if not all_bs:
            return {}

        # 使用相同的射线投射逻辑
        target_layers = self._classify_layer(new_verts) if use_winding else None

        result = {}
        for name, sources in all_bs.items():
            # 合并同名 BS delta 到 SuperMesh 级
            super_delta = np.zeros((self._super_verts.shape[0], 3), dtype=np.float64)
            for mesh_idx, delta in sources:
                offset = self._vert_offset[mesh_idx]
                n_v = delta.shape[0]
                super_delta[offset:offset + n_v] = delta

            if use_deformation_gradient:
                logger.info(f"Using Deformation Gradient for BS: {name}")
                full_delta = self._wrap_deform(new_verts, target_layers, super_delta, k=max(1, idw_k))
            else:
                # 采用级联投射 + IDW 混合直接插值位移向量 (Delta P)
                bary_delta, valid_mask = self._ray_cast_with_layer_filter(
                    new_verts, new_normals, target_layers, max_normal_angle_deg,
                    field_values=super_delta
                )

                n_hit = int(valid_mask.sum())
                if n_hit == 0:
                    continue

                # IDW 混合
                if idw_k > 0 and idw_blend > 0:
                    idw_delta = self._idw_sample(new_verts, idw_k, field_values=super_delta)
                    full_delta = (1.0 - idw_blend) * bary_delta + idw_blend * idw_delta
                else:
                    full_delta = bary_delta

            if np.any(np.abs(full_delta) > 1e-7):
                result[name] = full_delta

        logger.info(f"sample_bs_deltas: {len(result)} targets sampled")
        return result

    # ═══════════════════════════════════════════════════════════
    # 内部算法
    # ═══════════════════════════════════════════════════════════

    def _classify_layer(self, query_points):
        """
        用 Generalized Winding Number 判定每个查询点属于第几层壳。

        Returns
        -------
        layers : ndarray (N,) int — 四舍五入到最近整数层
        """
        import igl

        query_points = np.asarray(query_points, dtype=np.float64)
        V = np.asarray(self._super_verts, dtype=np.float64)
        F = np.asarray(self._super_faces, dtype=np.int64)

        winding = igl.fast_winding_number(V, F, query_points)
        layers = np.round(winding).astype(np.intp)

        logger.info(f"Winding Number: min={winding.min():.2f}, "
                    f"max={winding.max():.2f}, "
                    f"layers={np.unique(layers).tolist()}")
        return layers

    def _ray_cast_with_layer_filter(self, points, normals, target_layers,
                                     max_angle_deg, field_values=None):
        """
        两轮投射 + 法线过滤 + Winding Number 层级筛选。

        Pass 1: igl.point_mesh_squared_distance 最近面（严格法线阈值）
        Pass 2: KDTree k=16 候选面 + 放宽法线阈值（120°）捞回未命中点

        Returns
        -------
        out_values : ndarray (N, D) — 初步值（未命中位置为 0）
        valid_mask : ndarray (N,) bool — 哪些点成功命中
        """
        import igl
        from scipy.spatial import cKDTree

        if field_values is None:
            field_values = self._super_weights

        N = points.shape[0]
        dim = field_values.shape[1]
        out_values = np.zeros((N, dim), dtype=np.float64)
        valid_mask = np.zeros(N, dtype=bool)

        V = np.asarray(self._super_verts, dtype=np.float64)
        F = np.asarray(self._super_faces, dtype=np.int64)
        Q = np.asarray(points, dtype=np.float64)

        # 预计算面法线和面质心
        face_normals = igl.per_face_normals(V, F, np.array([0.0, 0.0, 1.0]))
        centroids = (V[F[:, 0]] + V[F[:, 1]] + V[F[:, 2]]) / 3.0

        cos_strict = np.cos(np.radians(max_angle_deg))

        # ═══ Pass 1: igl 最近面（快速批量） ═══
        sq_dists, face_ids, closest_pts = igl.point_mesh_squared_distance(Q, V, F)

        fvi = F[face_ids]
        A, B, C = V[fvi[:, 0]], V[fvi[:, 1]], V[fvi[:, 2]]
        bary = igl.barycentric_coordinates(closest_pts, A, B, C)

        dot_p1 = np.sum(normals * face_normals[face_ids], axis=1)
        accept_p1 = dot_p1 >= cos_strict

        if target_layers is not None:
            hit_wn = self._classify_layer(closest_pts)
            accept_p1 = accept_p1 & (hit_wn == target_layers)

        valid_p1 = np.where(accept_p1)[0]
        if len(valid_p1) > 0:
            v0, v1, v2 = fvi[valid_p1, 0], fvi[valid_p1, 1], fvi[valid_p1, 2]
            b = bary[valid_p1]
            out_values[valid_p1] = (b[:, 0:1] * field_values[v0] +
                                 b[:, 1:2] * field_values[v1] +
                                 b[:, 2:3] * field_values[v2])
            valid_mask[valid_p1] = True

        # ═══ Pass 2: KDTree 多候选面捞回未命中 ═══
        missed = np.where(~valid_mask)[0]
        if len(missed) > 0:
            k_candidates = min(16, len(centroids))
            tree = cKDTree(centroids)
            _, cand_indices = tree.query(Q[missed], k=k_candidates)
            if k_candidates == 1:
                cand_indices = cand_indices[:, np.newaxis]

            cos_relaxed = np.cos(np.radians(min(max_angle_deg + 30, 150.0)))

            for qi_local, qi_global in enumerate(missed):
                qn = normals[qi_global]
                qn_norm = np.linalg.norm(qn)
                if qn_norm < 1e-10:
                    fi = cand_indices[qi_local, 0]
                    cp = _project_to_triangle(Q[qi_global], V, F[fi])
                    bc = igl.barycentric_coordinates(
                        cp.reshape(1, 3),
                        V[F[fi, 0]].reshape(1, 3),
                        V[F[fi, 1]].reshape(1, 3),
                        V[F[fi, 2]].reshape(1, 3)
                    )[0]
                    v0, v1, v2 = F[fi]
                    out_values[qi_global] = (bc[0] * field_values[v0] +
                                          bc[1] * field_values[v1] +
                                          bc[2] * field_values[v2])
                    valid_mask[qi_global] = True
                    continue

                qn = qn / qn_norm
                best_fi = -1
                best_dist = np.inf

                need_layer_check = (target_layers is not None
                                    and len(self.meshes) > 1)

                for ci in range(k_candidates):
                    fi = cand_indices[qi_local, ci]
                    fn = face_normals[fi]
                    if np.dot(qn, fn) < cos_relaxed:
                        continue
                    if need_layer_check:
                        cp = _project_to_triangle(Q[qi_global], V, F[fi])
                        cp_wn = self._classify_layer(cp.reshape(1, 3))[0]
                        if cp_wn != target_layers[qi_global]:
                            continue
                    d = np.linalg.norm(Q[qi_global] - centroids[fi])
                    if d < best_dist:
                        best_dist = d
                        best_fi = fi

                if best_fi >= 0:
                    cp = _project_to_triangle(Q[qi_global], V, F[best_fi])
                    bc = igl.barycentric_coordinates(
                        cp.reshape(1, 3),
                        V[F[best_fi, 0]].reshape(1, 3),
                        V[F[best_fi, 1]].reshape(1, 3),
                        V[F[best_fi, 2]].reshape(1, 3)
                    )[0]
                    v0, v1, v2 = F[best_fi]
                    out_values[qi_global] = (bc[0] * field_values[v0] +
                                          bc[1] * field_values[v1] +
                                          bc[2] * field_values[v2])
                    valid_mask[qi_global] = True

        # ═══ Pass 3: 兜底最近面 ═══
        still_missed = np.where(~valid_mask)[0]
        if len(still_missed) > 0:
            if len(self.meshes) <= 1:
                sq_d2, fi2, cp2 = igl.point_mesh_squared_distance(
                    Q[still_missed], V, F)
                fvi2 = F[fi2]
                bc2 = igl.barycentric_coordinates(
                    cp2, V[fvi2[:, 0]], V[fvi2[:, 1]], V[fvi2[:, 2]])
                for i, qi in enumerate(still_missed):
                    v0, v1, v2 = fvi2[i]
                    b = bc2[i]
                    out_values[qi] = (b[0] * field_values[v0] +
                                   b[1] * field_values[v1] +
                                   b[2] * field_values[v2])
                    valid_mask[qi] = True
            else:
                if target_layers is None:
                    target_layers = self._classify_layer(Q)
                face_centroid_layers = self._classify_layer(centroids)
                for qi in still_missed:
                    ql = target_layers[qi]
                    same_layer = np.where(face_centroid_layers == ql)[0]
                    if len(same_layer) == 0:
                        same_layer = np.arange(len(F))
                    F_sub = F[same_layer]
                    sq_d, fi_sub, cp = igl.point_mesh_squared_distance(
                        Q[qi:qi+1], V, F_sub)
                    fvi_s = F_sub[fi_sub[0]]
                    bc = igl.barycentric_coordinates(
                        cp, V[fvi_s[0]:fvi_s[0]+1],
                        V[fvi_s[1]:fvi_s[1]+1],
                        V[fvi_s[2]:fvi_s[2]+1])[0]
                    out_values[qi] = (bc[0] * field_values[fvi_s[0]] +
                                   bc[1] * field_values[fvi_s[1]] +
                                   bc[2] * field_values[fvi_s[2]])
                    valid_mask[qi] = True
            logger.info(f"Pass 3: {len(still_missed)} fallback nearest-face")

        return out_values, valid_mask


        sq_dists, face_ids, closest_pts = igl.point_mesh_squared_distance(Q, V, F)

        fvi = F[face_ids]
        A, B, C = V[fvi[:, 0]], V[fvi[:, 1]], V[fvi[:, 2]]
        bary = igl.barycentric_coordinates(closest_pts, A, B, C)

        face_normals = igl.per_face_normals(V, F, np.array([0.0, 0.0, 1.0]))
        cos_threshold = np.cos(np.radians(max_angle_deg))
        dot_products = np.sum(normals * face_normals[face_ids], axis=1)
        accept = dot_products >= cos_threshold

        if target_layers is not None:
            hit_winding = self._classify_layer(closest_pts)
            accept = accept & (hit_winding == target_layers)

        valid_idx = np.where(accept)[0]
        if len(valid_idx) > 0:
            v0, v1, v2 = fvi[valid_idx, 0], fvi[valid_idx, 1], fvi[valid_idx, 2]
            b = bary[valid_idx]
            values[valid_idx] = (b[:, 0:1] * super_values[v0] +
                                 b[:, 1:2] * super_values[v1] +
                                 b[:, 2:3] * super_values[v2])
            valid_mask[valid_idx] = True

        return values, valid_mask

    def _heat_diffuse_on_topology(self, verts, faces, anchor_idx, anchor_vals,
                                   backend='heat'):
        """
        在目标 mesh 自身拓扑上做热扩散补全。

        Parameters
        ----------
        verts : (N, 3) — 目标 mesh 顶点
        faces : (M, 3) — 目标 mesh 三角面
        anchor_idx : (A,) — 锚点索引
        anchor_vals : (A, D) — 锚点值
        backend : 'heat' | 'scipy'

        Returns
        -------
        full_vals : (N, D)
        """
        N = verts.shape[0]
        D = anchor_vals.shape[1]

        if backend == 'heat':
            try:
                import potpourri3d as pp3d
                solver = pp3d.MeshVectorHeatSolver(
                    np.asarray(verts, dtype=np.float64),
                    np.asarray(faces, dtype=np.intp)
                )
                full_vals = np.zeros((N, D), dtype=np.float64)
                for d in range(D):
                    col = anchor_vals[:, d].astype(np.float64)
                    if np.all(col == 0):
                        continue
                    full_vals[:, d] = solver.extend_scalar(
                        anchor_idx.astype(np.intp), col
                    )
                return full_vals
            except Exception as e:
                logger.warning(f"Heat diffusion failed: {e}, fallback to scipy")
                backend = 'scipy'

        if backend == 'scipy':
            from core.laplacian_diffuse import laplacian_diffuse_scipy
            return laplacian_diffuse_scipy(verts, faces, anchor_idx,
                                           anchor_vals, D)

        raise ValueError(f"Unknown diffusion backend: {backend}")

    def _laplacian_smooth(self, verts, faces, values, iterations=1):
        """
        余切 Laplacian 平滑：在新 mesh 自身拓扑上平滑采样值。
        防止相邻顶点从不同源面采样导致的权重突变。
        """
        try:
            import robust_laplacian
            L, M = robust_laplacian.mesh_laplacian(
                np.asarray(verts, dtype=np.float64),
                np.asarray(faces, dtype=np.intp)
            )
        except ImportError:
            from core.laplacian_diffuse import _cotan_laplacian
            L = _cotan_laplacian(verts, faces)

        import scipy.sparse as sp

        # 构建邻接平均矩阵
        N = verts.shape[0]
        adj = sp.csr_matrix(L)
        adj.data = np.ones_like(adj.data)
        degree = np.array(adj.sum(axis=1)).flatten()
        degree[degree == 0] = 1.0
        D_inv = sp.diags(1.0 / degree)
        avg_matrix = D_inv @ adj

        alpha = 0.15  # 平滑强度（降低以保留投射精度）
        for _ in range(iterations):
            smoothed = avg_matrix @ values
            values = (1 - alpha) * values + alpha * smoothed

        return values

    def _kdtree_fallback(self, query_points):
        """KDTree 最近点后备方案（所有射线都 miss 时使用）。"""
        from scipy.spatial import cKDTree

        tree = cKDTree(self._super_verts)
        dists, idx = tree.query(query_points, k=3)

        # 距离加权
        eps = 1e-10
        inv_dist = 1.0 / (dists + eps)
        w = inv_dist / inv_dist.sum(axis=1, keepdims=True)

        N = query_points.shape[0]
        weights = np.zeros((N, self.num_joints), dtype=np.float64)
        for k in range(3):
            weights += w[:, k:k+1] * self._super_weights[idx[:, k]]

        return weights

    def _idw_sample(self, query_points, k=8, target_layers=None, field_values=None):
        """
        K 近邻 IDW (Inverse Distance Weighting) 采样。
        """
        from scipy.spatial import cKDTree

        if field_values is None:
            field_values = self._super_weights

        k = min(k, self._super_verts.shape[0])
        tree = cKDTree(self._super_verts)
        dists, idx = tree.query(query_points, k=k)
        if k == 1:
            dists = dists[:, np.newaxis]
            idx = idx[:, np.newaxis]

        eps = 1e-10
        inv_dist = 1.0 / (dists + eps)
        w = inv_dist / inv_dist.sum(axis=1, keepdims=True)

        N = query_points.shape[0]
        dim = field_values.shape[1]
        out_values = np.zeros((N, dim), dtype=np.float64)

        for ki in range(k):
            out_values += w[:, ki:ki+1] * field_values[idx[:, ki]]

        return out_values

    def _wrap_deform(self, query_points, target_layers, bs_delta, k=4):
        """
        基于仿射变换的形变传递 (类似 Maya Wrap Deformer)。
        通过构建源网格表面的局部坐标系 (M_base)，在形变后重建该坐标系 (M_target)，
        从而准确传递缩放和旋转，而不仅仅是线性位移。
        """
        from scipy.spatial import cKDTree
        import numpy as np

        V_base = self._super_verts
        F = self._super_faces
        V_target = V_base + bs_delta

        centroids = (V_base[F[:, 0]] + V_base[F[:, 1]] + V_base[F[:, 2]]) / 3.0
        tree = cKDTree(centroids)

        Q = query_points
        N = Q.shape[0]

        # 查找 K 个最近的面
        dists, face_idx = tree.query(Q, k=k)
        if k == 1:
            dists = dists[:, np.newaxis]
            face_idx = face_idx[:, np.newaxis]

        # 多源层级过滤：如果某个面的层级与目标点层级不符，给它一个极大的距离惩罚
        if target_layers is not None and len(self.meshes) > 1:
            face_layers = self._classify_layer(centroids)
            for i in range(k):
                fi = face_idx[:, i]
                fl = face_layers[fi]
                mismatch = (fl != target_layers)
                dists[mismatch, i] += 1e6

        # 距离反比权重
        eps = 1e-10
        inv_dist = 1.0 / (dists + eps)
        w = inv_dist / inv_dist.sum(axis=1, keepdims=True)

        new_Q = np.zeros_like(Q)

        for i in range(k):
            fi = face_idx[:, i]
            
            # Base Frame
            A = V_base[F[fi, 0]]
            B = V_base[F[fi, 1]]
            C = V_base[F[fi, 2]]
            
            E1 = B - A
            E2 = C - A
            N_vec = np.cross(E1, E2)
            area_base = np.linalg.norm(N_vec, axis=1, keepdims=True)
            area_base_safe = np.maximum(area_base, 1e-12)
            N_norm = N_vec / area_base_safe
            
            # 形状 (N, 3, 3) -> 列向量 E1, E2, N_norm
            M_base = np.stack([E1, E2, N_norm], axis=-1)
            
            # 伪逆矩阵求解局部坐标 (N, 3, 3)
            try:
                M_base_inv = np.linalg.inv(M_base)
            except np.linalg.LinAlgError:
                M_base_inv = np.linalg.pinv(M_base)

            # 计算局部坐标 alpha, beta, gamma
            QA = Q - A
            coords = np.matmul(M_base_inv, QA[:, :, np.newaxis]).squeeze(-1)
            
            # Target Frame (Deformed)
            A_t = V_target[F[fi, 0]]
            B_t = V_target[F[fi, 1]]
            C_t = V_target[F[fi, 2]]
            
            E1_t = B_t - A_t
            E2_t = C_t - A_t
            N_vec_t = np.cross(E1_t, E2_t)
            area_target = np.linalg.norm(N_vec_t, axis=1, keepdims=True)
            
            # 保持体积缩放：沿法线方向的缩放比例等于面积的平方根缩放
            scale = np.sqrt(area_target / area_base_safe)
            N_norm_t = (N_vec_t / np.maximum(area_target, 1e-12)) * scale
            
            M_target = np.stack([E1_t, E2_t, N_norm_t], axis=-1)
            
            # 重建形变后的世界坐标
            Q_t = A_t + np.matmul(M_target, coords[:, :, np.newaxis]).squeeze(-1)
            
            new_Q += w[:, i:i+1] * Q_t

        return new_Q - Q


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _compute_vertex_normals(verts, faces):
    """从三角面计算顶点法线。"""
    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)

    normals = np.zeros_like(verts)
    for i in range(3):
        np.add.at(normals, faces[:, i], fn)

    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return normals / norms


def _normalize_weights(weights):
    """Clamp ≥ 0 并归一化到行和 = 1。"""
    weights = np.maximum(weights, 0)
    row_sums = weights.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    return weights / row_sums


def _project_to_triangle(point, verts, face):
    """将点投影到三角面上最近位置。"""
    a = verts[face[0]]
    b = verts[face[1]]
    c = verts[face[2]]
    ab = b - a
    ac = c - a
    ap = point - a
    d1 = np.dot(ab, ap)
    d2 = np.dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return a.copy()
    bp = point - b
    d3 = np.dot(ab, bp)
    d4 = np.dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return b.copy()
    cp = point - c
    d5 = np.dot(ab, cp)
    d6 = np.dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return c.copy()
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return a + v * ab
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return a + w * ac
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return b + w * (c - b)
    denom = 1.0 / (va + vb + vc)
    v = vb * denom
    w = vc * denom
    return a + ab * v + ac * w
