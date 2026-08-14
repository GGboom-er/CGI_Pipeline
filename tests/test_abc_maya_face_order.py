import unittest

from cgi_pipeline.core.abc_reader import _convert_face_order_for_maya


class MayaFaceOrderTests(unittest.TestCase):
    def test_maya_face_order_reverses_each_polygon_and_uvs(self):
        face_counts = [3, 4]
        face_indices = [0, 1, 2, 3, 4, 5, 6]
        uv_indices = [10, 11, 12, 13, 14, 15, 16]

        maya_faces, maya_uvs, maya_normals = _convert_face_order_for_maya(
            face_counts, face_indices, uv_indices, []
        )

        self.assertEqual(maya_faces, [2, 1, 0, 6, 5, 4, 3])
        self.assertEqual(maya_uvs, [12, 11, 10, 16, 15, 14, 13])
        self.assertEqual(maya_normals, [])

    def test_maya_face_order_reverses_normal_triples_with_faces(self):
        face_counts = [3, 4]
        face_indices = [0, 1, 2, 3, 4, 5, 6]
        normals = [
            component
            for value in range(7)
            for component in (value, 0, 1)
        ]

        _maya_faces, _maya_uvs, maya_normals = _convert_face_order_for_maya(
            face_counts, face_indices, [], normals
        )

        expected_order = [2, 1, 0, 6, 5, 4, 3]
        expected = [
            component
            for value in expected_order
            for component in (value, 0, 1)
        ]
        self.assertEqual(maya_normals, expected)

    def test_missing_or_mismatched_normals_fall_back_without_failure(self):
        face_counts = [3]
        face_indices = [0, 1, 2]

        _faces, _uvs, missing = _convert_face_order_for_maya(
            face_counts, face_indices, [], []
        )
        _faces, _uvs, mismatched = _convert_face_order_for_maya(
            face_counts, face_indices, [], [0.0, 1.0, 0.0]
        )

        self.assertEqual(missing, [])
        self.assertEqual(mismatched, [])


if __name__ == "__main__":
    unittest.main()
