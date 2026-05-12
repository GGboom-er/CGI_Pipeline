"""
非刚性配准预处理模块 (Non-Rigid Registration)

解决痛点：消除动画师导出模型时带有的微小 Pose 偏移。
使用算法：基于 pycpd 的 Coherent Point Drift。
"""

import numpy as np
import logging
import time

logger = logging.getLogger(__name__)

def align_pose_non_rigid(source_verts, target_verts, max_iterations=50, tolerance=0.001, w=0.0):
    """
    使用非刚性 ICP (pycpd) 将 source_verts (Rig) 贴合到 target_verts (Asset)。
    
    该过程会将 source 当作柔性体进行形变吸附，返回吸附后的 source 坐标。
    这消除了导出的微小 Pose 误差，为后续精确的 KDTree/匈牙利算法对比提供完美的几何对齐基准。
    
    Parameters:
    -----------
    source_verts: ndarray (N, 3), 原始绑定的旧模型顶点坐标
    target_verts: ndarray (M, 3), 带有微小 Pose 偏移的新资产顶点坐标
    max_iterations: int, 最大迭代次数
    tolerance: float, 收敛阈值
    w: float, 噪点/离群点权重 (0~1.0)，应对点数不同非常关键
    
    Returns:
    --------
    aligned_verts: ndarray (N, 3), 贴合新姿态后的旧模型顶点坐标
    """
    try:
        from pycpd import DeformableRegistration
    except ImportError:
        logger.warning("pycpd 未安装，跳过非刚性配准。请使用 pip install pycpd 安装。")
        return source_verts
        
    t0 = time.time()
    
    # pycpd 要求 numpy float64 数组
    X = np.asarray(target_verts, dtype=np.float64)
    Y = np.asarray(source_verts, dtype=np.float64)
    
    if len(X) == 0 or len(Y) == 0:
        return source_verts

    logger.info(f"开始非刚性配准 (CPD): Source={len(Y)} verts, Target={len(X)} verts")
    
    # alpha 越大，形变越趋向于刚性 (平滑度限制)
    # beta 越大，形变范围越广
    # w (outlier) 越高，越能容忍两边点数或结构不一致
    reg = DeformableRegistration(**{'X': X, 'Y': Y, 'max_iterations': max_iterations, 'tolerance': tolerance, 'alpha': 2.0, 'beta': 2.0, 'w': w})
    
    # 注册回调以打印进度（可选）
    # def print_progress(iteration, error, X, Y):
    #     logger.debug(f"CPD 迭代 {iteration}: 误差 {error:.6f}")
    # reg.register_callback(print_progress)
    
    try:
        TY, _ = reg.register()
    except Exception as e:
        logger.error(f"非刚性配准计算失败: {e}")
        return source_verts
        
    logger.info(f"非刚性配准完成，耗时 {time.time() - t0:.2f}s")
    
    return TY
