import numpy as np
from core.laplacian_diffuse import laplacian_diffuse

def build_grid_adjacency(grid_size):
    adjacency = []
    for i in range(grid_size):
        for j in range(grid_size):
            idx = i * grid_size + j
            nbrs = []
            if i > 0: nbrs.append((i - 1) * grid_size + j)
            if i < grid_size - 1: nbrs.append((i + 1) * grid_size + j)
            if j > 0: nbrs.append(i * grid_size + (j - 1))
            if j < grid_size - 1: nbrs.append(i * grid_size + (j + 1))
            adjacency.append(nbrs)
    return adjacency

def test():
    grid_size = 10
    num_verts = grid_size * grid_size
    num_joints = 2
    
    adjacency = build_grid_adjacency(grid_size)
    
    # 4 corners as anchors
    anchor_indices = [
        0,                                # Top-Left
        grid_size - 1,                    # Top-Right
        (grid_size - 1) * grid_size,      # Bottom-Left
        num_verts - 1                     # Bottom-Right
    ]
    
    anchor_weights = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
        [0.0, 1.0],
        [1.0, 0.0]
    ])
    
    final_weights = laplacian_diffuse(
        adjacency, anchor_indices, anchor_weights, num_verts, num_joints,
        outer_rounds=3, smooth_rounds=8, smooth_blend=0.5, diffuse_rounds=150
    )
    
    # Check middle point (should be ~ [0.5, 0.5])
    mid_idx = (grid_size // 2) * grid_size + (grid_size // 2)
    print(f"Middle point weights: {final_weights[mid_idx]}")
    
    assert np.allclose(final_weights[mid_idx], [0.5, 0.5], atol=0.1), "Center point should average to 0.5"
    print("Test passed!")

if __name__ == "__main__":
    test()
