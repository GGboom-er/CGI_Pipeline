import sys
import os
import numpy as np
import logging

logging.basicConfig(level=logging.INFO)

# Add parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.deformation_field import DeformationField

def test_deformation_gradient_bs():
    # 构造一个极简的源网格 (Base Mesh)
    # 一个简单的四边形（2个三角形）
    base_verts = np.array([
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [1.0, 1.0, 0.0],
        [0.0, 1.0, 0.0]
    ])
    base_faces = np.array([
        [0, 1, 2],
        [0, 2, 3]
    ])
    
    # 构造一个表情 (BlendShape)
    # 假设把右边的一条边拉长并沿Z轴翘起
    bs_deltas = {
        "smile": np.array([
            [0.0, 0.0, 0.0],
            [0.5, 0.0, 0.5],
            [0.5, 0.5, 0.5],
            [0.0, 0.0, 0.0]
        ])
    }
    
    rig_data = {
        'all_joints': ['root'],
        'meshes': [{
            'vertices': base_verts,
            'faces': base_faces,
            'bs_deltas': bs_deltas
        }]
    }
    
    # 构造一个新的高模网格 (Target Mesh)
    # 比原来的网格更细分，或者有不同拓扑
    target_verts = np.array([
        [0.2, 0.2, 0.0],
        [0.8, 0.2, 0.0],
        [0.8, 0.8, 0.0],
        [0.2, 0.8, 0.0],
        [0.5, 0.5, 0.0]  # 中心点
    ])
    target_faces = np.array([
        [0, 1, 4],
        [1, 2, 4],
        [2, 3, 4],
        [3, 0, 4]
    ])
    
    print("Building DeformationField...")
    field = DeformationField(rig_data)
    
    print("Sampling BS Deltas using Deformation Gradient...")
    # use_deformation_gradient=True 默认开启
    result = field.sample_bs_deltas(
        target_verts, target_faces, 
        use_winding=False, 
        use_deformation_gradient=True,
        idw_k=1 # 强制使用单一最近面重建局部坐标系，方便验证
    )
    
    print("Result Deltas for 'smile':")
    smile_delta = result.get('smile')
    print(smile_delta)
    
    assert smile_delta is not None, "Failed to sample BS delta"
    assert smile_delta.shape == target_verts.shape, "Shape mismatch"
    print("✅ test_deformation_gradient_bs passed!")

if __name__ == "__main__":
    test_deformation_gradient_bs()
