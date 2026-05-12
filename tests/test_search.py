import time
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment

def test_bijection():
    N = 5000
    pts_old = np.random.rand(N, 3) * 100
    
    # Shuffle and add noise
    shuffle_idx = np.random.permutation(N)
    pts_new = pts_old[shuffle_idx] + np.random.randn(N, 3) * 0.001
    
    # Method 1: Greedy KDTree
    t0 = time.time()
    tree = cKDTree(pts_old)
    dists, indices = tree.query(pts_new, k=1)
    
    unique_indices = set()
    conflicts = []
    for i, idx in enumerate(indices):
        if idx in unique_indices:
            conflicts.append(i)
        else:
            unique_indices.add(idx)
            
    t1 = time.time()
    print(f"Greedy KDTree time: {t1-t0:.4f}s. Bijective match: {len(unique_indices)/N:.4f}")
    
    # Method 2: linear_sum_assignment (Hungarian) on sparse distance matrix
    # Since dense NxN is O(N^3) time and O(N^2) space, we can query k=5 and build sparse matrix
    t2 = time.time()
    _, indices_k = tree.query(pts_new, k=5)
    
    from scipy import sparse
    row = np.repeat(np.arange(N), 5)
    col = indices_k.flatten()
    data = np.linalg.norm(np.repeat(pts_new, 5, axis=0) - pts_old[col], axis=1)
    
    cost_matrix = sparse.csr_matrix((data, (row, col)), shape=(N, N))
    
    # scipy's linear_sum_assignment requires dense matrices, but we can use scipy.sparse.csgraph.min_weight_full_bipartite_matching
    from scipy.sparse import csgraph
    row_ind, col_ind = csgraph.min_weight_full_bipartite_matching(cost_matrix)
    
    t3 = time.time()
    print(f"Sparse Hungarian time: {t3-t2:.4f}s.")
    
if __name__ == "__main__":
    test_bijection()
