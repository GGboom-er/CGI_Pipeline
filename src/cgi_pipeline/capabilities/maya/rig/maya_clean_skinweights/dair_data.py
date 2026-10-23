"""NumPy-only DAIR adaptation policy and data preparation; safe inside Maya."""
import numpy as np

ALGORITHM_VERSION = "dair-tiered-4.0.0"
ANGLES_DEGREES = (-30.0, -15.0, 15.0, 30.0)
BETA = 10.0
REGULARIZATION = 1e-4
SWEEPS = 2
SWAPS = 2
SOLVER_ITERATIONS = 50
EPSILON = 1e-12
INPUT_NEGATIVE_MASS_TOLERANCE = 1e-7


def output_floor(threshold, support_count):
    """Keep fitted positive weights above tau with a small round-trip margin.

The margin is a numerical policy, not a second pruning threshold. Capping it
by the support's uniform weight keeps the constrained simplex feasible.
"""
    guard = max(1e-10, abs(float(np.spacing(threshold))) * 32)
    return min(threshold + guard, 1.0 / support_count)


def neighbor_vertices(edges, primary, rings, count):
    """Expand unique topology rings; never cross disconnected shells."""
    if not rings or not len(primary):
        return np.empty(0, dtype=np.int64)
    edges = np.asarray(edges, dtype=np.int64).reshape((-1, 2))
    selected = np.zeros(count, dtype=bool)
    selected[primary] = True
    initial = selected.copy()
    for _ in range(rings):
        selected[np.unique(edges[np.any(selected[edges], axis=1)])] = True
    return np.flatnonzero(selected & ~initial)


def compress_poses(influence_paths, sample_pivots):
    """Return descendant masks and pivot moments, preserving pose normalization."""
    groups = {}
    for path, pivot in sorted(sample_pivots.items()):
        mask = tuple(name == path or name.startswith(path + "|") for name in influence_paths)
        if not any(mask) or all(mask):
            continue
        groups.setdefault(mask, []).append(pivot)
    masks, moments = [], []
    for mask, pivots in sorted(groups.items()):
        values = np.asarray(pivots, dtype=float)
        masks.append(mask)
        moments.append((len(values), *values.sum(axis=0), float(np.square(values).sum())))
    return (np.asarray(masks, dtype=float).reshape((-1, len(influence_paths))),
            np.asarray(moments, dtype=float).reshape((-1, 5)), len(sample_pivots) * 12 + 1)


def proportional_baseline(weights, threshold, maximum):
    values = np.asarray(weights, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("skin weights must be finite")
    negative_mass = -np.minimum(values, 0).sum(axis=1)
    if np.any(negative_mass > INPUT_NEGATIVE_MASS_TOLERANCE):
        raise ValueError("skin weight row negative mass exceeds numerical tolerance 1e-7")
    values = np.maximum(values, 0)
    totals = values.sum(axis=1)
    if (not np.isfinite(values).all() or np.any(values < 0) or
            not np.isfinite(totals).all() or np.any(totals <= 0)):
        raise ValueError("finite, nonnegative, nonempty skin weight rows are required")
    original = values / totals[:, None]
    eligible = original > threshold
    if np.any(~eligible.any(axis=1)):
        raise ValueError("threshold would empty a row")
    output = np.where(eligible, original, 0)
    # Influence-table width is an upper bound: no per-row cap scan or sort is
    # necessary for a skin whose entire table already fits the requested cap.
    over = (np.flatnonzero(np.count_nonzero(eligible, axis=1) > maximum)
            if original.shape[1] > maximum else np.empty(0, dtype=np.int64))
    if len(over):
        order = np.argsort(-output[over], axis=1, kind="stable")
        rows = np.repeat(over, original.shape[1] - maximum)
        output[rows, order[:, maximum:].ravel()] = 0
    output /= output.sum(axis=1)[:, None]
    support_changed = np.any((output > 0) != (original > 0), axis=1)
    changed = np.flatnonzero(support_changed | np.any(np.abs(output - original) > EPSILON, axis=1))
    return original, output, eligible, changed


def tiered_filter(weights, threshold, maximum):
    """Threshold cleanup precedes cap selection, without pose fitting."""
    original, clean, _, threshold_rows = proportional_baseline(weights, threshold, np.shape(weights)[1])
    cap = (np.flatnonzero(np.count_nonzero(clean, axis=1) > maximum)
           if clean.shape[1] > maximum else np.empty(0, dtype=np.int64))
    return original, clean, threshold_rows, cap


def smooth_cleanup_delta(original, cleaned, edges, primary, rings, maximum, threshold, delta):
    """Spread only edit deltas over unique topology fronts with fixed support."""
    output = cleaned.copy()
    edges = np.asarray(edges, dtype=np.int64).reshape((-1, 2))
    selected = np.zeros(len(output), dtype=bool)
    selected[primary] = True
    all_neighbors = []
    for ring in range(rings):
        crossing = edges[selected[edges[:, 0]] ^ selected[edges[:, 1]]]
        if not len(crossing):
            break
        src = np.where(selected[crossing[:, 0]], crossing[:, 0], crossing[:, 1])
        dst = np.where(selected[crossing[:, 0]], crossing[:, 1], crossing[:, 0])
        targets, inverse = np.unique(dst, return_inverse=True)
        shifts = np.zeros((len(targets), output.shape[1]))
        np.add.at(shifts, inverse, output[src] - original[src])
        shifts /= np.bincount(inverse)[:, None]
        base = original[targets]
        support = base > threshold
        shifts *= support
        shifts -= base * shifts.sum(axis=1)[:, None]
        shifts /= ring + 2
        feasible = (np.count_nonzero(base, axis=1) <= maximum) & np.all((base == 0) | support, axis=1)
        largest = np.max(np.abs(shifts), axis=1)
        factor = np.minimum(1., (delta / (ring + 1)) / np.maximum(largest, 1e-300))
        floor = np.minimum(base, threshold + max(1e-10, abs(float(np.spacing(threshold))) * 32))
        available = np.where(shifts < 0, (base - floor) / np.maximum(-shifts, 1e-300), np.inf)
        factor = np.minimum(factor, available.min(axis=1))
        factor[~feasible] = 0
        output[targets] = base + shifts * factor[:, None]
        selected[targets] = True
        all_neighbors.extend(targets.tolist())
    return output, np.asarray(all_neighbors, dtype=np.int64)
