import copy
import importlib.util
import json
import math
import os
import tempfile
import unittest

import imath
from alembic import Abc, AbcGeom

from cgi_pipeline.core import abc_reader


IDENTITY = [[float(row == col) for col in range(4)] for row in range(4)]
RX90 = [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]]


class NormalSpaceTests(unittest.TestCase):
    def test_identity_normalizes_without_reordering(self):
        actual = abc_reader.transform_normals_fv(
            [0, 2, 0, 0, 0, -4], IDENTITY, expected_corners=2
        )
        self.assertEqual(actual, [0, 1, 0, 0, 0, -1])

    def test_row_vector_rotation_matches_transformed_geometry(self):
        self.assertEqual(
            abc_reader.transform_normals_fv([0, 0, 1], RX90, 1), [0, -1, 0]
        )

    def test_nonuniform_scale_and_shear_preserve_perpendicularity(self):
        matrix = [[2, 0.5, 0, 0], [0, 3, 0.75, 0],
                  [0.25, 0, 0.5, 0], [7, -9, 4, 1]]
        tangent = (1, 1, 0)
        normal = (1, -1, 1)
        transformed_tangent = [sum(tangent[i] * matrix[i][j] for i in range(3))
                               for j in range(3)]
        actual = abc_reader.transform_normals_fv(normal, matrix, 1)
        self.assertAlmostEqual(sum(a * b for a, b in zip(
            actual, transformed_tangent)), 0.0, places=12)
        self.assertAlmostEqual(math.hypot(*actual), 1.0, places=12)

    def test_translation_does_not_change_normal(self):
        matrix = [list(row) for row in IDENTITY]
        matrix[3] = [9, -3, 17, 1]
        self.assertEqual(
            abc_reader.transform_normals_fv([1, 0, 0], matrix, 1), [1, 0, 0]
        )

    def test_missing_normals_are_allowed_but_not_invalid_matrices(self):
        self.assertEqual(abc_reader.transform_normals_fv([], IDENTITY, 3), [])
        self.assertEqual(abc_reader.validate_normals_fv(None, 3), [])
        matrix = [list(row) for row in IDENTITY]
        matrix[2][2] = 0
        with self.assertRaisesRegex(ValueError, 'singular'):
            abc_reader.transform_normals_fv([], matrix, 3)

    def test_reflection_is_explicitly_unsupported(self):
        matrix = [list(row) for row in IDENTITY]
        matrix[0][0] = -1
        with self.assertRaisesRegex(ValueError, 'reflection'):
            abc_reader.transform_normals_fv([1, 0, 0], matrix, 1)

    def test_invalid_matrix_fails(self):
        for matrix in ([[1, 0], [0, 1]],
                       [[float('nan'), 0, 0, 0]] + IDENTITY[1:],
                       [IDENTITY[0], IDENTITY[1], [0, 0, 0, 0], IDENTITY[3]],
                       [[1, 0, 0, 1]] + IDENTITY[1:]):
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                abc_reader.transform_normals_fv([0, 0, 1], matrix, 1)

    def test_invalid_explicit_normal_values_fail(self):
        for values in ([0, 0, 0], [0, 1], [0, float('inf'), 1],
                       [float('nan'), 1, 0], ['invalid', 1, 0]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                abc_reader.validate_normals_fv(values, 1)
        with self.assertRaisesRegex(ValueError, 'corner'):
            abc_reader.validate_normals_fv([1, 0, 0], 2)


class MeshPreflightTests(unittest.TestCase):
    def setUp(self):
        self.mesh = {'vertices': 3, 'vert_positions': [0, 0, 0, 1, 0, 0, 0, 1, 0],
                     'face_counts': [3], 'face_indices': [0, 1, 2]}

    def test_missing_uv_and_normal_data_is_valid_and_not_modified(self):
        before = copy.deepcopy(self.mesh)
        self.assertIsNone(abc_reader.validate_mesh_entry(self.mesh))
        self.assertEqual(self.mesh, before)

    def test_complete_authored_data_is_valid(self):
        self.mesh.update(u_array=[0, 1, 0], v_array=[0, 0, 1],
                         uv_indices=[0, 1, 2], normals_fv=[0, 0, 1] * 3)
        abc_reader.validate_mesh_entry(self.mesh)

    def test_invalid_geometry_uv_and_normal_data_fail_before_mutation(self):
        invalid_fields = [
            ('vertices', True), ('vertices', 4), ('vert_positions', None),
            ('vert_positions', [float('nan')] * 9), ('face_counts', [2]),
            ('face_counts', [4]), ('face_indices', [0, 1, 3]),
            ('face_indices', [0, 1, 2.0]), ('u_array', [0]),
            ('uv_indices', [0, 0, 0]), ('normals_fv', [0, 0, 0] * 3),
        ]
        for name, value in invalid_fields:
            with self.subTest(name=name, value=value):
                mesh = dict(self.mesh, **{name: value})
                with self.assertRaises(ValueError):
                    abc_reader.validate_mesh_entry(mesh)
        for update in ({'u_array': [0], 'v_array': [0], 'uv_indices': [0, 1, 0]},
                       {'u_array': [float('inf')], 'v_array': [0], 'uv_indices': [0]*3}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                abc_reader.validate_mesh_entry(dict(self.mesh, **update))


def write_native_mesh(path, normals=None, scope=None, normal_indices=None,
                      transforms=(), positions=None):
    archive = Abc.OArchive(path)
    parent = archive.getTop()
    for name, matrix, inherits in transforms:
        parent = AbcGeom.OXform(parent, name)
        xform_sample = AbcGeom.XformSample()
        xform_sample.setMatrix(imath.M44d(*[value for row in matrix for value in row]))
        xform_sample.setInheritsXforms(inherits)
        parent.getSchema().set(xform_sample)
    mesh = AbcGeom.OPolyMesh(parent, 'mesh')
    positions = positions or ((0, 0, 0), (1, 0, 0), (0, 1, 0))
    points = imath.V3fArray(3)
    for index, value in enumerate(positions):
        points[index] = imath.V3f(*value)
    indices, counts = imath.IntArray(3), imath.IntArray(1)
    for index in range(3):
        indices[index] = index
    counts[0] = 3
    sample = AbcGeom.OPolyMeshSchemaSample(points, indices, counts)
    if normals is not None:
        values = imath.V3fArray(len(normals))
        for index, value in enumerate(normals):
            values[index] = imath.V3f(*value)
        normal_sample = AbcGeom.ON3fGeomParamSample()
        normal_sample.setVals(values)
        normal_sample.setScope(scope)
        if normal_indices is not None:
            normal_ids = imath.UnsignedIntArray(len(normal_indices))
            for index, value in enumerate(normal_indices):
                normal_ids[index] = value
            normal_sample.setIndices(normal_ids)
        sample.setNormals(normal_sample)
    mesh.getSchema().set(sample)


class NativeAlembicTransformTests(unittest.TestCase):
    def test_lightweight_mirrored_and_singular_affine_points_are_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            for x_scale in (-1, 0):
                with self.subTest(x_scale=x_scale):
                    matrix = [[x_scale, 0, 0, 0], [0, 2, 0, 0],
                              [0, 0, 3, 0], [7, -4, 5, 1]]
                    path = os.path.join(directory, f'scale_{x_scale}.abc')
                    write_native_mesh(path, transforms=[('xform', matrix, True)])
                    mesh = abc_reader.read_abc_as_info(path, lightweight=True)[
                        'meshes']['|xform|meshShape']
                    self.assertEqual(mesh, {
                        'vertices': 3,
                        'vert_positions': [7, -4, 5, 7 + x_scale, -4, 5, 7, -2, 5],
                    })

    def test_complete_read_still_rejects_mirrored_and_singular_matrices(self):
        with tempfile.TemporaryDirectory() as directory:
            for x_scale, reason in ((-1, 'reflection'), (0, 'singular')):
                with self.subTest(x_scale=x_scale):
                    matrix = [list(row) for row in IDENTITY]
                    matrix[0][0] = x_scale
                    path = os.path.join(directory, f'scale_{x_scale}.abc')
                    write_native_mesh(path, transforms=[('xform', matrix, True)])
                    with self.assertRaisesRegex(ValueError, reason):
                        abc_reader.read_abc_as_info(path)

    def test_lightweight_skips_explicit_normal_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'invalid_normals.abc')
            write_native_mesh(path, [(0, 0, 0)] * 3,
                              AbcGeom.GeometryScope.kFacevaryingScope)
            mesh = abc_reader.read_abc_as_info(path, lightweight=True)['meshes'][
                '|meshShape']
            self.assertEqual(set(mesh), {'vertices', 'vert_positions'})
            with self.assertRaisesRegex(ValueError, 'zero-length'):
                abc_reader.read_abc_as_info(path)

    def test_nonfinite_and_projective_matrices_fail_in_both_read_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            for kind, row, column, value, reason in (
                    ('nonfinite', 0, 0, float('nan'), 'non-finite'),
                    ('projective', 0, 3, 0.25, 'affine')):
                matrix = [list(values) for values in IDENTITY]
                matrix[row][column] = value
                path = os.path.join(directory, f'{kind}.abc')
                write_native_mesh(path, transforms=[('xform', matrix, True)])
                for lightweight in (True, False):
                    with self.subTest(kind=kind, lightweight=lightweight):
                        with self.assertRaisesRegex(ValueError, reason):
                            abc_reader.read_abc_as_info(path, lightweight=lightweight)

    def test_nonfinite_transformed_points_fail_in_both_read_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in (float('nan'), float('inf')):
                path = os.path.join(directory, f'point_{value}.abc')
                write_native_mesh(path, positions=((value, 0, 0), (1, 0, 0), (0, 1, 0)))
                for lightweight in (True, False):
                    with self.subTest(value=value, lightweight=lightweight):
                        with self.assertRaisesRegex(ValueError, 'finite'):
                            abc_reader.read_abc_as_info(path, lightweight=lightweight)

    def test_native_sample_inheritance_resets_parent_for_points_and_normals(self):
        parent = [list(row) for row in RX90]
        parent[3] = [10, 20, 30, 1]
        child = [[2, 0, 0, 0], [0, 3, 0, 0],
                 [0, 0, 4, 0], [1, 2, 3, 1]]
        grandchild = [list(row) for row in IDENTITY]
        grandchild[3] = [5, 0, 0, 1]
        with tempfile.TemporaryDirectory() as directory:
            for inherits, expected_points, expected_normal in (
                    (False, [11, 2, 3, 13, 2, 3, 11, 5, 7], [0, -0.8, 0.6]),
                    (True, [21, 17, 32, 23, 17, 32, 21, 13, 35], [0, -0.6, -0.8])):
                path = os.path.join(directory, f'inherits_{inherits}.abc')
                write_native_mesh(
                    path, [(0, -1, 1)] * 3, AbcGeom.GeometryScope.kFacevaryingScope,
                    transforms=[('parent', parent, True), ('child', child, inherits),
                                ('grandchild', grandchild, True)],
                    positions=((0, 0, 0), (1, 0, 0), (0, 1, 1)))
                archive = Abc.IArchive(path)
                native_parent = AbcGeom.IXform(archive.getTop(), 'parent')
                native_child = AbcGeom.IXform(native_parent, 'child')
                self.assertEqual(native_child.getSchema().getValue().getInheritsXforms(),
                                 inherits)
                del native_child, native_parent, archive
                for lightweight in (True, False):
                    with self.subTest(inherits=inherits, lightweight=lightweight):
                        mesh = abc_reader.read_abc_as_info(path, lightweight=lightweight)[
                            'meshes']['|parent|child|grandchild|meshShape']
                        self.assertEqual(mesh['vert_positions'], expected_points)
                        if not lightweight:
                            for actual, expected in zip(mesh['normals_fv'],
                                                        expected_normal * 3):
                                self.assertAlmostEqual(actual, expected, places=12)

    def test_noninheriting_child_ignores_unsupported_ancestor_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            for x_scale in (-1, 0):
                with self.subTest(x_scale=x_scale):
                    parent = [list(row) for row in IDENTITY]
                    parent[0][0] = x_scale
                    path = os.path.join(directory, f'reset_{x_scale}.abc')
                    write_native_mesh(path, transforms=[('parent', parent, True),
                                                        ('child', IDENTITY, False)])
                    mesh = abc_reader.read_abc_as_info(path)['meshes'][
                        '|parent|child|meshShape']
                    self.assertEqual(mesh['vert_positions'], [0, 0, 0, 1, 0, 0, 0, 1, 0])


class NativeAlembicNormalTests(unittest.TestCase):
    def test_native_scope_and_index_api(self):
        self.assertTrue(hasattr(AbcGeom.GeometryScope, 'kFacevaryingScope'))
        self.assertTrue(hasattr(AbcGeom.IN3fGeomParam, 'getIndexedValue'))
        self.assertTrue(hasattr(AbcGeom.IN3fGeomParam, 'isIndexed'))
        self.assertTrue(hasattr(AbcGeom.IN3fGeomParam, 'getScope'))

    def test_native_missing_and_valid_facevarying_normals(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, 'missing.abc')
            write_native_mesh(missing)
            self.assertEqual(abc_reader.read_abc_as_info(missing)['meshes'][
                '|meshShape']['normals_fv'], [])
            authored = os.path.join(directory, 'authored.abc')
            write_native_mesh(authored, [(0, 0, 2), (0, 3, 0), (4, 0, 0)],
                              AbcGeom.GeometryScope.kFacevaryingScope)
            self.assertEqual(abc_reader.read_abc_as_info(authored)['meshes'][
                '|meshShape']['normals_fv'], [1, 0, 0, 0, 1, 0, 0, 0, 1])

    def test_native_unsupported_scope_and_damaged_data_fail(self):
        cases = [
            ([(0, 0, 1)] * 3, AbcGeom.GeometryScope.kVertexScope, None, 'scope'),
            ([(0, 0, 1)], AbcGeom.GeometryScope.kUniformScope, None, 'scope'),
            ([(0, 0, 1)], AbcGeom.GeometryScope.kFacevaryingScope, None, 'corner'),
            ([(0, 0, 0)] * 3, AbcGeom.GeometryScope.kFacevaryingScope, None, 'zero'),
            ([(0, 0, 1)], AbcGeom.GeometryScope.kFacevaryingScope, [0, 1, 0], 'indices'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for index, (normals, scope, indices, message) in enumerate(cases):
                with self.subTest(case=index):
                    path = os.path.join(directory, f'invalid_{index}.abc')
                    write_native_mesh(path, normals, scope, indices)
                    with self.assertRaisesRegex(ValueError, message):
                        abc_reader.read_abc_as_info(path)


ORIGINAL_ABC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'projects',
    'ysj', '20261015_145428_liJing', '.info', 'chr_LiJing_std_hi.abc'))
BASELINE_READER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'projects',
    'ysj', 'PL276', 'normal_fix_baseline', 'abc_reader.py'))


@unittest.skipUnless(os.path.isfile(ORIGINAL_ABC) and os.path.isfile(BASELINE_READER),
                     'local original LiJing ABC and baseline are not installed')
class OriginalLiJingTests(unittest.TestCase):
    def test_original_archive_preserves_all_other_geometry_fields(self):
        spec = importlib.util.spec_from_file_location('baseline_abc_reader', BASELINE_READER)
        baseline_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline_module)
        before = baseline_module.read_abc_as_info(ORIGINAL_ABC)
        after = abc_reader.read_abc_as_info(ORIGINAL_ABC)
        self.assertEqual(before.keys(), after.keys())
        self.assertEqual(before['meshes'].keys(), after['meshes'].keys())
        for path in before['meshes']:
            original = dict(before['meshes'][path])
            corrected = dict(after['meshes'][path])
            original.pop('normals_fv')
            corrected.pop('normals_fv')
            self.assertEqual(original, corrected, path)
        archive = abc_reader._open_archive(ORIGINAL_ABC)
        scopes, matrices = {}, {}
        for path, obj, matrix in abc_reader._traverse(archive.getTop()):
            param = AbcGeom.IPolyMesh(obj.getParent(), obj.getName()).getSchema().getNormalsParam()
            scope = str(param.getScope()) if param.valid() else 'absent'
            scopes[scope] = scopes.get(scope, 0) + 1
            abc_reader._normal_transform_matrix(matrix)
            signature = tuple(round(float(matrix[row][col]), 8)
                              for row in range(4) for col in range(4))
            matrices[str(signature)] = matrices.get(str(signature), 0) + 1
        body_key = '|LiJing|geo|body|body_msh|Mesh_002Shape'
        old_normals = before['meshes'][body_key]['normals_fv']
        new_normals = after['meshes'][body_key]['normals_fv']
        self.assertEqual(len(old_normals), 85976 * 3)
        angles = []
        for index in range(0, len(old_normals), 3):
            x, y, z = old_normals[index:index + 3]
            expected = (x, -z, y)
            magnitude = math.hypot(*expected)
            dot = sum(a*b/magnitude for a, b in zip(expected, new_normals[index:index + 3]))
            angles.append(math.degrees(math.acos(max(-1, min(1, dot)))))
        self.assertLess(max(angles), 0.00001)
        print(json.dumps({'meshes': len(after['meshes']), 'normal_scopes': scopes,
                          'matrix_signatures': matrices, 'body_corners': len(angles),
                          'Rx90_expected_max_angle_deg': max(angles)}))


if __name__ == '__main__':
    unittest.main()
