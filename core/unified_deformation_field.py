"""
统一变形场 (Unified Deformation Field)

核心思想：
  把整套绑定（身体+衣服+配件）的所有蒙皮权重/BS delta 烘焙到 3D 空间中，
  构建一个连续的"变形场"。任何新几何体丢进来，从场中采样即可获得变形信息。

解决的问题：
  - 新资产拓扑/点序/形状变化
  - 模型合并/拆分
  - 层叠关系（背心在外套和身体之间）
  - 延伸结构（睫毛只有根部接触眼眶）
  - 自身形状保持（头发不被撑开）

使用流程：
  field = UnifiedDeformationField()
  field.add_source("body", body_verts, body_faces, body_weights, body_joints,
                   blendshapes={"smile": smile_delta, "blink": blink_delta})
  field.add_source("jacket", jacket_verts, jacket_faces, jacket_weights, jacket_joints)
  field.build()

  # 一次采样，同时获得权重 + BS delta
  result = field.query_full(new_verts, new_normals=new_normals, new_faces=new_faces)
  new_weights = result["weights"]        # (Q, J_union)
  new_deltas = result["blendshapes"]     # dict: target_name → (Q, 3)
  confidence = result["confidence"]      # (Q,)

依赖：
  numpy, scipy, igl (libigl python), robust_laplacian, potpourri3d
"""

import numpy as np
import logging
import time
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class UnifiedDeformationField:
    """
    统一变形场 — 从多个已绑定源模型构建空间插值场，
    对任意新几何体一次采样同时求解权重和 BS delta。
    """

    def __init__(self):
        self._sources: List[Dict] = []
        self._joint_union: List[str] = []
        self._joint_index: Dict[str, int] = {}
        self._bs_targets: List[str] = []  # BS target 名称 union
        self._bs_index: Dict[str, int] = {}

        # 构建后的场数据
        self._built = False
        self._all_points = None       # (N_total, 3)
        self._all_weights = None      # (N_total, J_union)
        self._all_normals = None      # (N_total, 3)
        self._all_layer = None        # (N_total,) int
        self._all_source_id = None    # (N_total,) int
        self._all_bs_deltas = None    # (N_total, T_union, 3) — 所有 BS target 的 delta
        self._kdtree = None
        self._interpolator = None

    # ═══════════════════════════════════════════════════════════════
    # 数据输入
    # ═══════════════════════════════════════════════════════════════

    def add_source(self, name: str, verts: np.ndarray, faces: np.ndarray,
                   weights: np.ndarray, joints: List[str],
                   normals: Optional[np.ndarray] = None,
                   blendshapes: Optional[Dict[str, np.ndarray]] = None):
        """
        添加一个已绑定的源模型到场中。

        Parameters
        ----------
        name : str
            源模型名称
        verts : (V, 3)
        faces : (F, 3) 三角面
        weights : (V, J) 蒙皮权重
        joints : list of str, len=J
        normals : (V, 3) optional
        blendshapes : dict, optional
            {target_name: (V, 3) delta} — 每个 BS target 的顶点位移
        """
        verts = np.asarray(verts, dtype=np.float64)
        faces = np.asarray(faces, dtype=np.intp)
        weights = np.asarray(weights, dtype=np.float64)

        if normals is None:
            normals = self._compute_vertex_normals(verts, faces)
        else:
            normals = np.asarray(normals, dtype=np.float64)

        # 注册骨骼到 union
        for j in joints:
            if j not in self._joint_index:
                self._joint_index[j] = len(self._joint_union)
                self._joint_union.append(j)

        # 注册 BS targets 到 union
        bs_data = {}
        if blendshapes:
            for tname, delta in blendshapes.items():
                delta = np.asarray(delta, dtype=np.float64)
                assert delta.shape == verts.shape, \
                    f"BS '{tname}' delta shape {delta.shape} != verts {verts.shape}"
                if tname not in self._bs_index:
                    self._bs_index[tname] = len(self._bs_targets)
                    self._bs_targets.append(tname)
                bs_data[tname] = delta

        source_id = len(self._sources)
        self._sources.append({
            "name": name,
            "verts": verts,
            "faces": faces,
            "weights": weights,
            "joints": joints,
            "normals": normals,
            "blendshapes": bs_data,
            "source_id": source_id,
        })
        self._built = False

        bs_count = len(bs_data)
        logger.info(f"Added source '{name}': {verts.shape[0]} verts, "
                    f"{len(joints)} joints, {bs_count} BS targets")

    @property
    def joint_union(self) -> List[str]:
        return self._joint_union

    @property
    def num_joints(self) -> int:
        return len(self._joint_union)

    @property
    def bs_targets(self) -> List[str]:
        return self._bs_targets

    @property
    def num_bs_targets(self) -> int:
        return len(self._bs_targets)

    # ═══════════════════════════════════════════════════════════════
    # 构建场
    # ═══════════════════════════════════════════════════════════════

    def build(self, method: str = "kdtree_weighted",
              compute_layers: bool = True):
        """
        构建空间插值场。

        Parameters
        ----------
        method : str
            "kdtree_weighted" — KDTree + 距离加权（快，推荐）
            "rbf" — RBF 插值（精确但慢，适合小规模）
        compute_layers : bool
            是否计算层级信息（用 fast_winding_number）
        """
        t0 = time.time()

        J_union = len(self._joint_union)
        T_union = len(self._bs_targets)

        all_points = []
        all_weights = []
        all_normals = []
        all_source_id = []
        all_bs_deltas = []

        for src in self._sources:
            V = src["verts"].shape[0]
            all_points.append(src["verts"])
            all_normals.append(src["normals"])
            all_source_id.append(np.full(V, src["source_id"], dtype=np.intp))

            # 映射局部权重到 union 列
            w_union = np.zeros((V, J_union), dtype=np.float64)
            for li, jname in enumerate(src["joints"]):
                gi = self._joint_index[jname]
                w_union[:, gi] = src["weights"][:, li]
            all_weights.append(w_union)

            # 映射 BS delta 到 union
            bs_union = np.zeros((V, T_union, 3), dtype=np.float64)
            for tname, delta in src["blendshapes"].items():
                ti = self._bs_index[tname]
                bs_union[:, ti, :] = delta
            all_bs_deltas.append(bs_union)

        self._all_points = np.vstack(all_points)
        self._all_weights = np.vstack(all_weights)
        self._all_normals = np.vstack(all_normals)
        self._all_source_id = np.concatenate(all_source_id)

        if T_union > 0:
            self._all_bs_deltas = np.concatenate(all_bs_deltas, axis=0)  # (N_total, T, 3)
        else:
            self._all_bs_deltas = None

        N_total = self._all_points.shape[0]
        logger.info(f"Field: {N_total} points, {J_union} joints, {T_union} BS targets")

        # 计算层级
        if compute_layers:
            self._all_layer = self._compute_layers()
        else:
            self._all_layer = np.zeros(N_total, dtype=np.intp)

        # 构建空间索引
        from scipy.spatial import cKDTree
        self._kdtree = cKDTree(self._all_points)

        # RBF
        if method == "rbf":
            self._build_rbf()

        self._method = method
        self._built = True
        logger.info(f"Field built in {time.time()-t0:.2f}s (method={method})")

    # ═══════════════════════════════════════════════════════════════
    # 核心：计算插值系数
    # ═══════════════════════════════════════════════════════════════

    def _compute_sample_coefficients(self, points, normals=None,
                                      k=12, sigma=0.0,
                                      normal_weight=0.3,
                                      layer_filter=True):
        """
        计算查询点对源点的插值系数。

        这是所有查询的核心 — 权重和 BS delta 共享同一套系数。

        Returns
        -------
        coeffs : (Q, k) float
            归一化的插值系数
        indices : (Q, k) int
            对应的源点索引
        """
        points = np.asarray(points, dtype=np.float64)
        Q = points.shape[0]

        N_src = self._all_points.shape[0]
        k = min(k, N_src)
        dists, indices = self._kdtree.query(points, k=k)

        if k == 1:
            dists = dists[:, np.newaxis]
            indices = indices[:, np.newaxis]

        # 自动 sigma
        if sigma <= 0:
            sigma = max(np.median(dists[:, -1]), 1e-6)

        # 距离权重
        coeffs = np.exp(-(dists ** 2) / (2 * sigma ** 2))

        # 法线一致性加权
        if normals is not None and normal_weight > 0:
            normals = np.asarray(normals, dtype=np.float64)
            src_normals = self._all_normals[indices]  # (Q, k, 3)
            cos_sim = np.einsum("qi,qki->qk", normals, src_normals)
            normal_factor = np.maximum(cos_sim, 0) ** 2
            coeffs *= (1 - normal_weight) + normal_weight * normal_factor

        # 层级过滤
        if layer_filter and self._all_layer is not None:
            query_layer = self._estimate_query_layer(points)
            src_layers = self._all_layer[indices]
            layer_diff = np.abs(src_layers - query_layer[:, np.newaxis])
            layer_factor = np.exp(-layer_diff.astype(np.float64))
            coeffs *= layer_factor

        # 归一化
        w_sum = coeffs.sum(axis=1, keepdims=True)
        w_sum[w_sum == 0] = 1.0
        coeffs /= w_sum

        return coeffs, indices

    # ═══════════════════════════════════════════════════════════════
    # 查询接口
    # ═══════════════════════════════════════════════════════════════

    def query(self, points: np.ndarray,
              normals: Optional[np.ndarray] = None,
              faces: Optional[np.ndarray] = None,
              k: int = 12, sigma: float = 0.0,
              normal_weight: float = 0.3,
              layer_filter: bool = True,
              shape_preserve: float = 0.0) -> np.ndarray:
        """
        查询权重。

        Returns
        -------
        weights : (Q, J_union) 归一化权重
        """
        assert self._built, "Call build() first"
        points = np.asarray(points, dtype=np.float64)
        Q = points.shape[0]
        J = len(self._joint_union)

        if Q == 0:
            return np.zeros((0, J))

        if self._method == "rbf":
            return self._query_rbf(points)

        coeffs, indices = self._compute_sample_coefficients(
            points, normals, k, sigma, normal_weight, layer_filter
        )

        # 加权插值权重
        src_weights = self._all_weights[indices]  # (Q, k, J)
        result = np.einsum("qk,qkj->qj", coeffs, src_weights)

        # 形状保持正则化
        if shape_preserve > 0 and faces is not None:
            result = self._apply_shape_preservation(
                points, faces, result, stiffness=shape_preserve
            )

        # Clamp & normalize
        result = np.maximum(result, 0)
        row_sums = result.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        result /= row_sums
        return result

    def query_blendshapes(self, points: np.ndarray,
                          normals: Optional[np.ndarray] = None,
                          targets: Optional[List[str]] = None,
                          k: int = 12, sigma: float = 0.0,
                          normal_weight: float = 0.3,
                          layer_filter: bool = True) -> Dict[str, np.ndarray]:
        """
        查询 BS delta。

        Parameters
        ----------
        targets : list of str, optional
            要查询的 BS target 名称。None = 全部。

        Returns
        -------
        deltas : dict {target_name: (Q, 3) ndarray}
        """
        assert self._built, "Call build() first"
        points = np.asarray(points, dtype=np.float64)
        Q = points.shape[0]

        if self._all_bs_deltas is None or len(self._bs_targets) == 0:
            return {}

        if targets is None:
            targets = self._bs_targets

        coeffs, indices = self._compute_sample_coefficients(
            points, normals, k, sigma, normal_weight, layer_filter
        )

        result = {}
        for tname in targets:
            if tname not in self._bs_index:
                continue
            ti = self._bs_index[tname]
            # (Q, k, 3) — 每个近邻在该 target 上的 delta
            src_deltas = self._all_bs_deltas[indices, ti, :]  # (Q, k, 3)
            # 加权插值
            interpolated = np.einsum("qk,qkd->qd", coeffs, src_deltas)
            result[tname] = interpolated

        return result

    def query_full(self, points: np.ndarray,
                   normals: Optional[np.ndarray] = None,
                   faces: Optional[np.ndarray] = None,
                   bs_targets: Optional[List[str]] = None,
                   k: int = 12, sigma: float = 0.0,
                   normal_weight: float = 0.3,
                   layer_filter: bool = True,
                   shape_preserve: float = 0.0) -> Dict:
        """
        一次采样，同时返回权重 + BS delta + 置信度。

        共享同一套插值系数，避免重复计算。

        Returns
        -------
        dict with keys:
            "weights" : (Q, J_union) 归一化权重
            "blendshapes" : dict {target_name: (Q, 3)}
            "confidence" : (Q,) float [0, 1]
            "joints" : list of str — 骨骼名称顺序
            "bs_targets" : list of str — BS target 名称顺序
        """
        assert self._built, "Call build() first"
        points = np.asarray(points, dtype=np.float64)
        Q = points.shape[0]
        J = len(self._joint_union)

        if Q == 0:
            return {
                "weights": np.zeros((0, J)),
                "blendshapes": {},
                "confidence": np.zeros(0),
                "joints": self._joint_union,
                "bs_targets": self._bs_targets,
            }

        # 计算插值系数（核心，只算一次）
        coeffs, indices = self._compute_sample_coefficients(
            points, normals, k, sigma, normal_weight, layer_filter
        )

        # === 权重 ===
        src_weights = self._all_weights[indices]
        weights = np.einsum("qk,qkj->qj", coeffs, src_weights)

        if shape_preserve > 0 and faces is not None:
            weights = self._apply_shape_preservation(
                points, faces, weights, stiffness=shape_preserve
            )

        weights = np.maximum(weights, 0)
        row_sums = weights.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        weights /= row_sums

        # === BS delta ===
        bs_result = {}
        if self._all_bs_deltas is not None and len(self._bs_targets) > 0:
            query_targets = bs_targets if bs_targets else self._bs_targets
            for tname in query_targets:
                if tname not in self._bs_index:
                    continue
                ti = self._bs_index[tname]
                src_deltas = self._all_bs_deltas[indices, ti, :]
                bs_result[tname] = np.einsum("qk,qkd->qd", coeffs, src_deltas)

        # === 置信度 ===
        dists_k1, _ = self._kdtree.query(points, k=1)
        src_dists, _ = self._kdtree.query(self._all_points, k=2)
        char_dist = max(np.median(src_dists[:, 1]), 1e-8)
        confidence = np.exp(-(dists_k1.flatten() ** 2) / (2 * (3 * char_dist) ** 2))

        return {
            "weights": weights,
            "blendshapes": bs_result,
            "confidence": confidence,
            "joints": list(self._joint_union),
            "bs_targets": list(self._bs_targets),
        }

    def query_with_confidence(self, points, **kwargs):
        """查询权重并返回置信度。"""
        points = np.asarray(points, dtype=np.float64)
        dists, _ = self._kdtree.query(points, k=1)
        src_dists, _ = self._kdtree.query(self._all_points, k=2)
        char_dist = max(np.median(src_dists[:, 1]), 1e-8)
        confidence = np.exp(-(dists.flatten() ** 2) / (2 * (3 * char_dist) ** 2))
        weights = self.query(points, **kwargs)
        return weights, confidence

    # ═══════════════════════════════════════════════════════════════
    # 延伸结构处理（睫毛/头发）
    # ═══════════════════════════════════════════════════════════════

    def query_with_geodesic_falloff(self, points, faces,
                                     anchor_indices, anchor_source="auto",
                                     falloff_distance=None,
                                     **query_kwargs):
        """
        对延伸结构（睫毛、头发）的特殊处理：
        1. 锚点（根部）从空间场获取权重
        2. 沿自身拓扑扩散，按测地距离衰减
        """
        points = np.asarray(points, dtype=np.float64)
        faces = np.asarray(faces, dtype=np.intp)
        V = points.shape[0]
        J = len(self._joint_union)

        if anchor_source == "auto":
            dists, _ = self._kdtree.query(points, k=1)
            median_d = np.median(dists)
            anchor_indices = np.where(dists.flatten() < median_d * 0.5)[0]
            if len(anchor_indices) == 0:
                anchor_indices = np.argsort(dists.flatten())[:max(1, V // 10)]

        anchor_indices = np.asarray(anchor_indices, dtype=np.intp)
        anchor_weights = self.query(points[anchor_indices], **query_kwargs)

        try:
            import potpourri3d as pp3d
            solver = pp3d.MeshHeatMethodDistanceSolver(points, faces)
            geo_dist = solver.compute_distance_multisource(anchor_indices)
        except ImportError:
            from scipy.spatial import cKDTree
            tree = cKDTree(points[anchor_indices])
            geo_dist, _ = tree.query(points, k=1)

        if falloff_distance is None:
            bbox = points.max(axis=0) - points.min(axis=0)
            falloff_distance = np.linalg.norm(bbox) * 0.3

        falloff = np.exp(-(geo_dist ** 2) / (2 * (falloff_distance / 3) ** 2))

        field_weights = self.query(points, **query_kwargs)

        from core.laplacian_diffuse import laplacian_diffuse_heat
        diffused = laplacian_diffuse_heat(points, faces, anchor_indices, anchor_weights, J)

        result = falloff[:, np.newaxis] * diffused + (1 - falloff[:, np.newaxis]) * field_weights

        result = np.maximum(result, 0)
        row_sums = result.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        result /= row_sums
        return result

    # ═══════════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════════

    def _compute_layers(self):
        """用 fast_winding_number 计算每个源点的层深度。"""
        try:
            import igl
        except ImportError:
            logger.warning("igl not available, skipping layer computation.")
            return np.zeros(self._all_points.shape[0], dtype=np.intp)

        N_total = self._all_points.shape[0]
        layers = np.zeros(N_total, dtype=np.intp)

        for src in self._sources:
            src_id = src["source_id"]
            src_mask = self._all_source_id == src_id
            query_pts = self._all_points[src_mask]

            for other_src in self._sources:
                if other_src["source_id"] == src_id:
                    continue
                V = other_src["verts"]
                F = other_src["faces"]
                if F.shape[0] == 0:
                    continue

                winding = igl.fast_winding_number(
                    V.astype(np.float32),
                    F.astype(np.int32),
                    query_pts.astype(np.float32)
                )
                inside = (np.abs(winding) > 0.5).astype(np.intp)
                layers[src_mask] += inside

        return layers

    def _estimate_query_layer(self, points):
        """估计查询点的层深度。"""
        try:
            import igl
        except ImportError:
            return np.zeros(points.shape[0], dtype=np.intp)

        layers = np.zeros(points.shape[0], dtype=np.intp)
        for src in self._sources:
            V = src["verts"]
            F = src["faces"]
            if F.shape[0] == 0:
                continue
            winding = igl.fast_winding_number(
                V.astype(np.float32),
                F.astype(np.int32),
                points.astype(np.float32)
            )
            layers += (np.abs(winding) > 0.5).astype(np.intp)

        return layers

    def _build_rbf(self):
        """构建 RBF 插值器。"""
        from scipy.interpolate import RBFInterpolator

        N = self._all_points.shape[0]
        if N > 5000:
            logger.warning(f"RBF with {N} points is slow. Subsampling to 5000.")
            idx = np.random.choice(N, 5000, replace=False)
            pts = self._all_points[idx]
            wts = self._all_weights[idx]
        else:
            pts = self._all_points
            wts = self._all_weights

        self._interpolator = RBFInterpolator(
            pts, wts,
            kernel="thin_plate_spline",
            smoothing=1e-4
        )

    def _query_rbf(self, points):
        """RBF 查询。"""
        result = self._interpolator(points)
        result = np.maximum(result, 0)
        row_sums = result.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        result /= row_sums
        return result

    def _apply_shape_preservation(self, verts, faces, raw_weights, stiffness=1.0):
        """
        Laplacian 正则化：确保权重过渡平滑且保持自身形状。
        解: (I + stiffness * L^T @ L) @ W = W_raw
        """
        import scipy.sparse as sp
        import scipy.sparse.linalg as spla

        try:
            import robust_laplacian
            L, M = robust_laplacian.mesh_laplacian(verts, faces)
        except ImportError:
            from core.laplacian_diffuse import _cotan_laplacian
            L = _cotan_laplacian(verts, faces)

        N = verts.shape[0]
        J = raw_weights.shape[1]

        LtL = (L.T @ L).tocsc()
        I = sp.eye(N, format="csc")
        A = I + stiffness * LtL

        result = np.zeros_like(raw_weights)
        try:
            LU = spla.splu(A)
            for j in range(J):
                result[:, j] = LU.solve(raw_weights[:, j])
        except RuntimeError:
            for j in range(J):
                result[:, j] = spla.lsqr(A, raw_weights[:, j])[0]

        return result

    @staticmethod
    def _compute_vertex_normals(verts, faces):
        """从面法线平均得到顶点法线。"""
        v0 = verts[faces[:, 0]]
        v1 = verts[faces[:, 1]]
        v2 = verts[faces[:, 2]]
        face_normals = np.cross(v1 - v0, v2 - v0)

        N = verts.shape[0]
        vertex_normals = np.zeros((N, 3))
        for i in range(3):
            np.add.at(vertex_normals, faces[:, i], face_normals)

        norms = np.linalg.norm(vertex_normals, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vertex_normals / norms
