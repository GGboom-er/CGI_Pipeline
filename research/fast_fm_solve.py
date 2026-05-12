"""
Fast Functional Map solver - direct least-squares formulation.
Mathematically equivalent to pyFM's fit() with L-BFGS-B, but solves in one shot.

The key insight: all three energy terms are quadratic in vec(C), so the whole
problem is a linear least-squares: min_x ||Ax - b||^2.

Vectorization convention: x = vec(C) with C stored column-major,
i.e. x = [C[:,0]; C[:,1]; ...; C[:,k1-1]], length = k2*k1.
"""
import time
import numpy as np


def build_descr_op(mesh, descr, k):
    """Compute multiplicative operators: pinv @ diag(descr_i) @ ev -> (k,k) per descriptor."""
    ev = mesh.eigenvectors[:, :k]
    pinv = ev.T @ mesh.A  # (k, n)
    ops = []
    for i in range(descr.shape[1]):
        ops.append(pinv @ (descr[:, i:i+1] * ev))  # (k, k)
    return ops


def fit_fm_direct(mesh1, mesh2, descr1, descr2, k1, k2,
                  w_descr=1.0, w_lap=1e-2, w_dcomm=1.0):
    """
    Solve for C (k2 x k1) directly via least-squares.

    vec(C) convention: column-major, x[col*k2 : (col+1)*k2] = C[:, col]

    Identity: vec(A @ X @ B) = (B^T ⊗ A) vec(X)
    So: vec(C @ D_A) = (D_A^T ⊗ I_k2) vec(C)
        vec(D_B @ C) = (I_k1 ⊗ D_B) vec(C)
    """
    t0 = time.time()
    n_vars = k2 * k1

    # Project descriptors onto LB basis
    descr1_red = (mesh1.eigenvectors[:, :k1].T @ mesh1.A @ descr1)  # (k1, p)
    descr2_red = (mesh2.eigenvectors[:, :k2].T @ mesh2.A @ descr2)  # (k2, p)

    # We'll build the normal equation: (A^T A) x = A^T b
    # Instead of stacking huge A matrix, accumulate A^T A and A^T b directly
    ATA = np.zeros((n_vars, n_vars))
    ATb = np.zeros(n_vars)

    I_k1 = np.eye(k1)
    I_k2 = np.eye(k2)

    # --- Term 1: descriptor preservation ---
    # E_descr = sum_j ||C @ a_j - b_j||^2
    # vec form: (a_j^T ⊗ I_k2) vec(C) = b_j
    # M_j = kron(a_j^T, I_k2) is (k2, k2*k1) — but a_j is (k1,1) so a_j^T is (1,k1)
    # kron((1,k1), (k2,k2)) = (k2, k2*k1)
    if w_descr > 0:
        for j in range(descr1_red.shape[1]):
            a_j = descr1_red[:, j]  # (k1,)
            b_j = descr2_red[:, j]  # (k2,)
            # M_j[row, col*k2 + row] = a_j[col]  (diagonal blocks)
            # M_j^T M_j = kron(a_j @ a_j^T, I_k2)
            # M_j^T b_j = kron(a_j, I_k2) @ b_j = vec of (b_j @ a_j^T) col-major
            ATA += w_descr * np.kron(np.outer(a_j, a_j), I_k2)
            ATb += w_descr * np.kron(a_j, b_j)

    # --- Term 2: LB commutativity ---
    # E_lap = sum_{i,j} (ev_sqdiff[i,j] * C[i,j])^2
    # This is diagonal in vec(C)
    if w_lap > 0:
        ev_sqdiff = np.square(
            mesh1.eigenvalues[None, :k1] - mesh2.eigenvalues[:k2, None]
        )  # (k2, k1)
        ev_sqdiff /= ev_sqdiff.sum()
        # In column-major vec: element (i,j) is at index j*k2 + i
        diag_vals = w_lap * ev_sqdiff.ravel(order='F')  # column-major
        ATA += np.diag(diag_vals)
        # ATb contribution is 0 (target is zero)

    # --- Term 3: descriptor commutativity ---
    # E_dcomm = sum_i ||C @ D_Ai - D_Bi @ C||_F^2
    # vec form: M_i vec(C) = 0, where M_i = kron(D_Ai^T, I_k2) - kron(I_k1, D_Bi)
    # M_i^T M_i = kron(D_Ai D_Ai^T, I_k2) + kron(I_k1, D_Bi^T D_Bi)
    #           - kron(D_Ai^T, D_Bi) - kron(D_Ai, D_Bi^T)
    if w_dcomm > 0:
        ops1 = build_descr_op(mesh1, descr1, k1)
        ops2 = build_descr_op(mesh2, descr2, k2)
        n_ops = len(ops1)
        w_dc = w_dcomm / n_ops  # normalize by number of operators

        for D_A, D_B in zip(ops1, ops2):
            # Accumulate M_i^T M_i directly using Kronecker product properties
            ATA += w_dc * np.kron(D_A @ D_A.T, I_k2)
            ATA += w_dc * np.kron(I_k1, D_B.T @ D_B)
            ATA -= w_dc * np.kron(D_A.T, D_B)
            ATA -= w_dc * np.kron(D_A, D_B.T)
        # ATb contribution is 0

    # --- Fix first column ---
    ev_sign = np.sign(mesh1.eigenvectors[0, 0] * mesh2.eigenvectors[0, 0])
    area_ratio = np.sqrt(mesh2.area / mesh1.area)
    c0 = np.zeros(k2)
    c0[0] = ev_sign * area_ratio

    # Partition: x = [x_fixed; x_free] where x_fixed = c0 (first k2 elements)
    # ATA @ x = ATb  =>  ATA[:, :k2] @ c0 + ATA[:, k2:] @ x_free = ATb
    # => ATA[k2:, k2:] @ x_free = ATb[k2:] - ATA[k2:, :k2] @ c0
    ATb_adj = ATb[k2:] - ATA[k2:, :k2] @ c0
    ATA_free = ATA[k2:, k2:]

    # Solve the reduced system
    x_free = np.linalg.solve(ATA_free, ATb_adj)

    # Reconstruct full solution
    x_full = np.concatenate([c0, x_free])

    # Reshape to C (k2, k1) from column-major vec
    C = x_full.reshape((k1, k2)).T

    elapsed = time.time() - t0
    return C, elapsed
