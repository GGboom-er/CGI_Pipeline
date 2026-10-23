"""Independent DAIR-inspired adaptation; exact compressed synthetic-LBS energy.

Each sampled ancestor rotates around three orthogonal axes at four fixed angles.
Summing these rotations analytically gives an isotropic quadratic. Ancestors with
the same descendant mask are compressed by pivot moments, without dropping poses.
SciPy solves each nonnegative, sum-one fixed-support QP; support exchange remains
a deterministic local search, not a global optimum or the author's implementation.
"""
from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.optimize import minimize
from .dair_data import (ALGORITHM_VERSION, ANGLES_DEGREES, BETA, REGULARIZATION,
                        SWEEPS, SWAPS, SOLVER_ITERATIONS, EPSILON,
                        compress_poses, proportional_baseline, neighbor_vertices, output_floor)


def build_coupling(points, edges, beta=BETA):
    """C = D + beta L.T D L includes every affected neighboring L row."""
    count = len(points)
    edges = np.asarray(edges, dtype=np.int64).reshape((-1, 2))
    if not len(edges):
        return sparse.eye(count, format="csr"), np.ones(count)
    lengths = np.linalg.norm(points[edges[:, 0]] - points[edges[:, 1]], axis=1)
    positive = lengths[lengths > EPSILON]
    floor = max(float(np.median(positive)) * 1e-8, EPSILON) if len(positive) else EPSILON
    rows = np.concatenate((edges[:, 0], edges[:, 1]))
    cols = np.concatenate((edges[:, 1], edges[:, 0]))
    distance = np.maximum(np.tile(lengths, 2), floor)
    adjacency = sparse.csr_matrix((1.0 / distance, (rows, cols)), shape=(count, count))
    totals = np.asarray(adjacency.sum(axis=1)).ravel()
    inverse = np.divide(1.0, totals, out=np.zeros(count), where=totals > 0)
    laplacian = sparse.diags((totals > 0).astype(float)) - sparse.diags(inverse) @ adjacency
    degree = np.bincount(rows, minlength=count)
    scales = np.divide(np.bincount(rows, weights=distance, minlength=count), degree,
                       out=np.ones(count), where=degree > 0)
    diagonal = sparse.diags(1.0 / np.maximum(scales, floor) ** 2)
    return (diagonal + beta * laplacian.T @ diagonal @ laplacian).tocsr(), scales


class DeformationMetric:
    """Pairwise pose Gram coefficients, compressed by identical subtree masks."""
    def __init__(self, points, masks, moments, pose_count):
        self.points = np.asarray(points, dtype=float)
        self.masks = np.asarray(masks, dtype=float)
        self.moments = np.asarray(moments, dtype=float)
        self.pose_count = int(pose_count)
        self.coefficient = 4 * np.sum(1 - np.cos(np.deg2rad(ANGLES_DEGREES))) / pose_count

    def pair(self, vertex, others):
        own = self.points[vertex]
        other = self.points[np.asarray(others)]
        count, px, py, pz, squared = self.moments.T
        pivot_sum = np.stack((px, py, pz), axis=1)
        return self.coefficient * ((other @ own)[:, None] * count -
                                   (other + own) @ pivot_sum.T + squared)


def _repair_sum(values, lower, upper):
    """Repair only solver roundoff inside bounds; never renormalize past tau."""
    result = np.clip(values, lower, upper)
    for _ in range(len(result) + 1):
        residual = 1.0 - result.sum()
        if residual == 0:
            break
        slack = upper - result if residual > 0 else result - lower
        index = int(np.argmax(slack))
        amount = min(abs(residual), slack[index])
        if amount <= 0:
            break
        result[index] += amount if residual > 0 else -amount
    if (abs(result.sum() - 1) > 1e-12 or np.any(result < lower) or
            np.any(result > upper) or np.any(result <= 0)):
        return None
    return result


def _solve_support(hessian, linear, support, initial, bounds=None, threshold=0.0):
    support = np.asarray(sorted(support), dtype=np.int64)
    h = hessian[np.ix_(support, support)]
    g = linear[support]
    start = np.maximum(initial[support], 0)
    start = start / start.sum() if start.sum() else np.full(len(support), 1 / len(support))
    if bounds is None:
        bounds = [(output_floor(threshold, len(support)), 1.0)] * len(support)
    if len(support) == 1:
        fitted = np.ones(1)
    else:
        lower, upper = np.asarray(bounds).T
        fitted = _box_simplex(h, g, start, lower, upper)
        if fitted is None:
            solved = minimize(lambda w: float(w @ h @ w + 2 * g @ w), start,
                          jac=lambda w: 2 * (h @ w + g), method="SLSQP",
                          bounds=bounds,
                          constraints={"type": "eq", "fun": lambda w: w.sum() - 1,
                                       "jac": lambda w: np.ones(len(w))},
                          options={"maxiter": SOLVER_ITERATIONS, "ftol": 1e-11})
            if not solved.success or not np.isfinite(solved.x).all():
                return initial.copy(), False
            fitted = solved.x
        fitted = _repair_sum(fitted, lower, upper)
        if fitted is None or np.any(fitted <= threshold):
            return initial.copy(), False
    output = np.zeros_like(initial)
    output[support] = fitted
    return output, True


def _box_simplex(h, g, initial, lower, upper):
    """Small convex QP by feasible active sets; SLSQP remains the fallback.

    Solve h*x+g+lambda=0 on free variables, sum(x)=1, with box bounds.
    Blocking variables enter the working set; wrong-sign multipliers leave it.
    """
    x = _repair_sum(initial, lower, upper)
    if x is None:
        return None
    fixed = np.zeros(len(x), dtype=np.int8)
    scale = max(1., float(np.max(np.abs(h))), float(np.max(np.abs(g))))
    tol = 1e-10 * scale
    for _ in range(8 * len(x) + 8):
        free = np.flatnonzero(fixed == 0)
        if not len(free):
            return None
        bound = np.flatnonzero(fixed != 0)
        matrix = np.empty((len(free) + 1, len(free) + 1))
        matrix[:-1, :-1] = h[np.ix_(free, free)]
        matrix[-1, :-1] = matrix[:-1, -1] = 1.
        matrix[-1, -1] = 0.
        rhs = np.r_[-g[free] - h[np.ix_(free, bound)] @ x[bound], 1 - x[bound].sum()]
        try:
            solution = np.linalg.solve(matrix, rhs)
        except np.linalg.LinAlgError:
            return None
        direction = solution[:-1] - x[free]
        limits = np.full(len(free), np.inf)
        positive, negative = direction > 1e-14, direction < -1e-14
        limits[positive] = (upper[free[positive]] - x[free[positive]]) / direction[positive]
        limits[negative] = (lower[free[negative]] - x[free[negative]]) / direction[negative]
        blocking = int(np.argmin(limits))
        step = min(1., max(0., float(limits[blocking])))
        x[free] += step * direction
        if step < 1 - 1e-12:
            vertex = free[blocking]
            fixed[vertex] = 1 if direction[blocking] > 0 else -1
            x[vertex] = upper[vertex] if fixed[vertex] == 1 else lower[vertex]
            continue
        residual = h @ x + g + solution[-1]
        violation = np.where(fixed == -1, -residual, np.where(fixed == 1, residual, 0))
        if violation.max() <= tol:
            return x
        fixed[int(np.argmax(violation))] = 0
    return None


def _score(row, hessian, linear):
    return float(row @ hessian @ row + 2 * linear @ row)


def _fit_row(hessian, linear, initial, eligible, maximum, threshold):
    current = initial.copy()
    support = np.flatnonzero(current > 0)
    fitted, ok = _solve_support(hessian, linear, support, current, threshold=threshold)
    failures = int(not ok)
    if _score(fitted, hessian, linear) < _score(current, hessian, linear):
        current = fitted
    for _ in range(SWAPS):
        support = np.flatnonzero(current > 0)
        entering = np.flatnonzero(eligible & (current == 0))
        if not len(entering):
            break
        gradient = hessian @ current + linear
        proposals = []
        for incoming in entering:
            for outgoing in support:
                amount = current[outgoing]
                curvature = max(EPSILON, hessian[incoming, incoming] + hessian[outgoing, outgoing] -
                                2 * hessian[incoming, outgoing])
                slope = gradient[incoming] - gradient[outgoing]
                transfer = np.clip(-slope / curvature, 0, amount)
                delta = (2 * transfer * slope + transfer ** 2 * curvature +
                         (amount - transfer) ** 2 * hessian[outgoing, outgoing])
                proposals.append((float(delta), int(incoming), int(outgoing)))
        best = current
        for _, incoming, outgoing in sorted(proposals)[:2]:
            chosen = [index for index in support if index != outgoing] + [incoming]
            fitted, ok = _solve_support(hessian, linear, chosen, current, threshold=threshold)
            failures += int(not ok)
            if _score(fitted, hessian, linear) < _score(best, hessian, linear) - 1e-13:
                best = fitted
        if np.array_equal(best, current):
            break
        current = best
    if np.count_nonzero(current) > maximum:
        raise RuntimeError("support search violated the influence cap")
    return current, failures


def evaluate_energy(original, output, active, coupling, metric, regularization=REGULARIZATION):
    delta = output[active] - original[active]
    coefficients = delta @ metric.masks.T
    local = coupling[active][:, active].tocsr()
    energy = regularization * float(np.square(delta).sum())
    for index, vertex in enumerate(active):
        row = local.getrow(index)
        pair = metric.pair(vertex, active[row.indices])
        energy += float(np.sum(row.data[:, None] * pair * coefficients[index] * coefficients[row.indices]))
    return energy


def optimize_weights(points, edges, weights, masks, moments, pose_count, threshold=.001,
                     maximum=12, beta=BETA, regularization=REGULARIZATION,
                     neighbor_rings=1, neighbor_max_delta=.02, coupling_override=None):
    original, baseline, eligible, primary = proportional_baseline(weights, threshold, maximum)
    neighbors = neighbor_vertices(edges, primary, neighbor_rings, len(original))
    counts = {"primary_count": len(primary), "neighbor_candidates": len(neighbors),
              "neighbor_rings": neighbor_rings, "neighbor_max_delta": neighbor_max_delta,
              "threshold": threshold, "max_influences": maximum}
    if not len(primary):
        return original.copy(), {"changed_vertices": 0, "baseline_error": 0.0,
                                 "optimized_error": 0.0, "solver_failures": 0,
                                 "neighbor_changed_count": 0, "max_neighbor_delta": 0.0, **counts}
    active = np.sort(np.r_[primary, neighbors])
    primary_mask = np.zeros(len(original), dtype=bool)
    primary_mask[primary] = True
    first_ring_mask = np.zeros(len(original), dtype=bool)
    first_ring_mask[neighbor_vertices(edges, primary, 1, len(original))] = True
    baseline[neighbors] = original[neighbors]
    if len(active) * len(masks) > 16_000_000:
        raise ValueError("deformation coefficient buffer exceeds the bounded 16M-value limit")
    coupling = build_coupling(np.asarray(points), edges, beta)[0] if coupling_override is None else coupling_override
    metric = DeformationMetric(points, masks, moments, pose_count)
    output = baseline.copy()
    coefficients = (output[active] - original[active]) @ metric.masks.T
    local = coupling[active][:, active].tocsr()
    baseline_objective = evaluate_energy(original, baseline, active, coupling, metric, regularization)
    baseline_error = evaluate_energy(original, baseline, active, coupling, metric, 0.0)
    failures = 0
    for _ in range(SWEEPS):
        for index, vertex in enumerate(active):
            row = local.getrow(index)
            own = int(np.flatnonzero(row.indices == index)[0])
            gram = metric.pair(vertex, active[row.indices])
            candidates = np.flatnonzero(eligible[vertex] if primary_mask[vertex] else original[vertex] > 0)
            masks_local = metric.masks[:, candidates]
            hessian = masks_local.T @ ((row.data[own] * gram[own])[:, None] * masks_local)
            hessian += regularization * np.eye(len(candidates))
            cross = np.sum(row.data[:, None] * gram * coefficients[row.indices], axis=0)
            cross -= row.data[own] * gram[own] * coefficients[index]
            original_coefficients = metric.masks @ original[vertex]
            linear = masks_local.T @ (cross - row.data[own] * gram[own] * original_coefficients)
            linear -= regularization * original[vertex, candidates]
            current = output[vertex, candidates]
            if primary_mask[vertex]:
                fitted, failed = _fit_row(hessian, linear, current, np.ones(len(candidates), dtype=bool), maximum, threshold)
            else:
                source = original[vertex, candidates]
                bound_delta = neighbor_max_delta if first_ring_mask[vertex] else neighbor_max_delta / 2
                lower = np.maximum(source - bound_delta, np.minimum(source, output_floor(threshold, len(candidates))))
                upper = np.minimum(source + bound_delta, 1.0)
                fitted, ok = _solve_support(hessian, linear, np.arange(len(candidates)), current,
                                           bounds=list(zip(lower, upper)), threshold=threshold)
                failed = int(not ok)
            failures += failed
            if _score(fitted, hessian, linear) <= _score(current, hessian, linear):
                output[vertex, candidates] = fitted
                coefficients[index] = metric.masks @ (output[vertex] - original[vertex])
    optimized_objective = evaluate_energy(original, output, active, coupling, metric, regularization)
    if optimized_objective > baseline_objective + max(1e-11, abs(baseline_objective) * 1e-9):
        raise RuntimeError("deformation-aware solution regressed against proportional baseline")
    optimized_error = evaluate_energy(original, output, active, coupling, metric, 0.0)
    # Regularization must not buy a lower composite objective by worsening the
    # deformation-only criterion. Preserve the known baseline in that case.
    kept_baseline = optimized_error > baseline_error + max(1e-12, abs(baseline_error) * 1e-9)
    if kept_baseline:
        output, optimized_error, optimized_objective = baseline, baseline_error, baseline_objective
    neighbor_delta = np.max(np.abs(output[neighbors] - original[neighbors]), axis=1) if len(neighbors) else np.empty(0)
    changed = np.any((output > 0) != (original > 0), axis=1) | np.any(np.abs(output - original) > EPSILON, axis=1)
    if len(neighbors) and (np.max(neighbor_delta) > neighbor_max_delta + 1e-10 or
                          not np.array_equal(output[neighbors] > 0, original[neighbors] > 0)):
        raise RuntimeError("neighbor edit violated its box bound or fixed positive support")
    effective = output / output.sum(axis=1)[:, None]
    if np.any((effective > 0) & (effective <= threshold)):
        raise RuntimeError("fitted output contains a positive effective weight at or below threshold")
    return output, {"changed_vertices": int(np.count_nonzero(changed)), "baseline_error": baseline_error,
                    "neighbor_changed_count": int(np.count_nonzero(neighbor_delta > EPSILON)),
                    "max_neighbor_delta": float(np.max(neighbor_delta)) if len(neighbors) else 0.0,
                    **counts,
                    "optimized_error": optimized_error, "solver_failures": failures,
                    "baseline_objective": baseline_objective, "optimized_objective": optimized_objective,
                    "kept_proportional_baseline": bool(kept_baseline),
                    "beta": beta, "regularization": regularization,
                    "sampled_pose_count": pose_count, "compressed_pose_groups": len(masks)}


def optimize_tiered(points, edges, weights, masks, moments, pose_count, threshold=.001,
                    maximum=12, neighbor_rings=2, neighbor_max_delta=.02):
    """Threshold cleanup is proportional; expensive fitting is cap-local only."""
    from .dair_data import tiered_filter, smooth_cleanup_delta
    original, clean, threshold_rows, cap = tiered_filter(weights, threshold, maximum)
    output, threshold_neighbors = smooth_cleanup_delta(original, clean, edges, threshold_rows,
        neighbor_rings, maximum, threshold, neighbor_max_delta)
    # Cap neighborhoods use the clean reference. Avoid accumulating two edits
    # on the same neighbors beyond their original permitted deviation.
    neighbors = neighbor_vertices(edges, cap, neighbor_rings, len(original))
    active = np.sort(np.r_[cap, neighbors])
    metrics = {"baseline_error": None, "optimized_error": None, "solver_failures": 0}
    if len(cap):
        columns = np.flatnonzero(np.any(clean[active] > 0, axis=0))
        mapping = np.full(len(original), -1, dtype=np.int64)
        mapping[active] = np.arange(len(active))
        edges = np.asarray(edges, dtype=np.int64).reshape((-1, 2))
        local_edges = mapping[edges[np.all(mapping[edges] >= 0, axis=1)]]
        # Keep all original L rows contributing to active variables. Cropping
        # first would incorrectly change degrees and boundary energy.
        coupling = build_coupling(points, edges)[0][active][:, active].tocsr()
        fitted, metrics = optimize_weights(np.asarray(points)[active], local_edges,
            clean[np.ix_(active, columns)], np.asarray(masks)[:, columns], moments, pose_count,
            threshold, maximum, neighbor_rings=neighbor_rings,
            neighbor_max_delta=neighbor_max_delta, coupling_override=coupling)
        output[active] = clean[active]
        output[np.ix_(active, columns)] = fitted
    changed = np.any(output != original, axis=1)
    all_primary = np.union1d(threshold_rows, cap)
    all_neighbors = np.setdiff1d(np.union1d(threshold_neighbors, neighbors), all_primary)
    delta = np.max(np.abs(output[all_neighbors] - original[all_neighbors]), axis=1) if len(all_neighbors) else np.empty(0)
    return output, {**metrics, "primary_count": len(all_primary), "threshold_count": len(threshold_rows),
        "cap_count": len(cap), "solver_vertex_count": len(active),
        "changed_vertices": int(np.count_nonzero(changed)), "neighbor_candidates": len(all_neighbors),
        "neighbor_changed_count": int(np.count_nonzero(delta > EPSILON)),
        "max_neighbor_delta": float(delta.max()) if len(delta) else 0.,
        "neighbor_rings": neighbor_rings, "neighbor_max_delta": neighbor_max_delta,
        "threshold": threshold, "max_influences": maximum,
        "error_scope": "cap-local reference after proportional threshold cleanup"}
