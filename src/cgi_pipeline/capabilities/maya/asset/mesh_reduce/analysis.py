"""Array-only libigl analysis, run in the registered numerical environment.

Component labels are analysis units, not Maya nodes or individual Reduce calls.
Distances are deterministic sampled estimates, never certified Hausdorff bounds.
"""
from __future__ import annotations

from time import perf_counter
import numpy as np


def group_shells(points, triangles, polygon_ids, group_count=2):
    """Group disconnected shells by world-space face density, preserving faces."""
    import igl
    from sklearn.cluster import KMeans
    from threadpoolctl import threadpool_limits
    started = perf_counter()
    points, triangles, polygon_ids = _mesh(points, triangles, polygon_ids)
    number, labels = igl.facet_components(triangles)
    area = igl.doublearea(points, triangles) / 2
    sums = np.bincount(labels, weights=area, minlength=number)
    owners = np.full(int(polygon_ids.max()) + 1, -1, dtype=np.int64)
    for label in range(number):
        faces = np.unique(polygon_ids[labels == label])
        if np.any(owners[faces] >= 0):
            raise ValueError('One polygon spans more than one edge-connected shell')
        owners[faces] = label
    counts = np.bincount(owners, minlength=number)
    if np.any(sums <= 0) or np.any(counts == 0):
        raise ValueError('Degenerate or empty source shell')
    density = counts / sums
    features = np.log(density)[:, None]
    if type(group_count) is not int or group_count < 1:
        raise ValueError('group_count must be a positive integer')
    groups = min(group_count, number, len(np.unique(features)))
    if groups < 1:
        raise ValueError('group_count must be positive')
    with threadpool_limits(limits=1):
        fitted = KMeans(n_clusters=groups, random_state=0, n_init=10).fit(features)
    order = np.argsort(fitted.cluster_centers_[:, 0])[::-1]
    remap = np.empty(groups, dtype=np.int64); remap[order] = np.arange(groups)
    shell_groups = remap[fitted.labels_]
    result = []
    for group in range(groups):
        ids = np.flatnonzero(shell_groups == group)
        result.append({'group': group, 'faces': np.flatnonzero(np.isin(owners, ids)).tolist(),
                       'shells': ids.tolist(), 'density_range': [float(density[ids].min()), float(density[ids].max())],
                       'surface_area': float(sums[ids].sum())})
    return {'groups': result, 'shell_count': number, 'triangle_shells': labels,
            'polygon_shells': owners, 'shell_density': density,
            'seconds': perf_counter() - started, 'method': 'libigl.facet_components + sklearn.KMeans(log world face density)',
            'scope': 'disconnected shells; continuous mixed-density segmentation not implemented'}


def _mesh(points, triangles, polygon_ids):
    points = np.ascontiguousarray(points, dtype=np.float64)
    triangles = np.ascontiguousarray(triangles, dtype=np.int64)
    polygon_ids = np.asarray(polygon_ids, dtype=np.int64)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError('Finite N by 3 points required')
    if triangles.ndim != 2 or triangles.shape[1] != 3 or len(triangles) == 0:
        raise ValueError('Nonempty triangular mesh required')
    if triangles.min() < 0 or triangles.max() >= len(points):
        raise ValueError('Triangle indices out of range')
    if len(polygon_ids) != len(triangles) or polygon_ids.min() < 0:
        raise ValueError('Triangle to original polygon mapping required')
    if not np.array_equal(np.unique(polygon_ids), np.arange(polygon_ids.max()+1)):
        raise ValueError('Polygon IDs must cover the source contiguously')
    return points, triangles, polygon_ids


def _samples(vertices, faces, seed, count):
    """All vertices and triangle centers, plus area-weighted surface samples."""
    import igl
    rng = np.random.default_rng(seed)
    area = igl.doublearea(vertices, faces)
    choices = rng.choice(len(faces), count, p=area/area.sum())
    bary = rng.random((count, 2)); flip = bary.sum(axis=1) > 1
    bary[flip] = 1-bary[flip]
    bary = np.column_stack((1-bary.sum(axis=1), bary))
    random = (vertices[faces[choices]] * bary[:, :, None]).sum(axis=1)
    return np.concatenate((vertices[np.unique(faces)], vertices[faces].mean(axis=1), random))


def compare_shells(points, triangles, triangle_shells, candidate_points, candidate_triangles,
                   source_vertex_ids, samples_per_shell=128, tolerance=None):
    """Bidirectional surface distances restricted to the same source shell."""
    import igl
    started = perf_counter()
    points = np.asarray(points, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int64)
    candidate_points = np.asarray(candidate_points, dtype=np.float64)
    candidate_triangles = np.asarray(candidate_triangles, dtype=np.int64)
    labels = np.asarray(triangle_shells, dtype=np.int64)
    ids = np.asarray(source_vertex_ids, dtype=np.int64)
    if len(ids) != len(candidate_points) or ids.min() < 0 or ids.max() >= len(points):
        raise ValueError('Invalid retained source vertex correspondence')
    vertex_shell = np.full(len(points), -1, dtype=np.int64)
    minimum = np.full(len(points), np.iinfo(np.int64).max, dtype=np.int64)
    np.maximum.at(vertex_shell, triangles.ravel(), np.repeat(labels, 3))
    np.minimum.at(minimum, triangles.ravel(), np.repeat(labels, 3))
    if np.any((vertex_shell >= 0) & (minimum != vertex_shell)):
        raise ValueError('Shells sharing a vertex require explicit region correspondence')
    target_labels = vertex_shell[ids[candidate_triangles]]
    if np.any(target_labels != target_labels[:, :1]):
        raise ValueError('Reduced face bridges different source shells')
    target_labels = target_labels[:, 0]
    rows = []
    for label in np.unique(labels):
        original = triangles[labels == label]
        candidate = candidate_triangles[target_labels == label]
        if not len(candidate):
            rows.append({'shell': int(label), 'missing': True})
            continue
        both = []
        worst = None
        for v, f, tv, tf in ((points, original, candidate_points, candidate),
                             (candidate_points, candidate, points, original)):
            query = _samples(v, f, int(label), samples_per_shell)
            tree = igl.AABB(); tree.init(tv, tf)
            squared, _, closest = tree.squared_distance(tv, tf, query)
            distance = np.sqrt(np.maximum(squared, 0))
            index = int(distance.argmax())
            location = {'distance': float(distance[index]), 'point': query[index].tolist(),
                        'closest': closest[index].tolist()}
            if worst is None or location['distance'] > worst['distance']:
                worst = location
            both.append(distance)
        distances = np.concatenate(both)
        row = {'shell': int(label), 'missing': False, 'max': float(distances.max()),
               'p95': float(np.quantile(distances, .95)), 'rms': float(np.sqrt(np.mean(distances**2))),
               'sample_count': len(distances), 'worst': worst}
        if tolerance is not None:
            row['exceeds_tolerance'] = row['max'] > tolerance
        rows.append(row)
    found = [r for r in rows if not r['missing']]
    return {'method': 'libigl.AABB bidirectional per-source-shell surface queries',
            'precision': 'sampled estimate; vertices + triangle centers + deterministic area samples',
            'shells': rows, 'missing_shells': sum(r['missing'] for r in rows),
            'max': max((r['max'] for r in found), default=None), 'tolerance': tolerance,
            'seconds': perf_counter()-started}
