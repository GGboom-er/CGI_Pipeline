"""测试 abc_reader 纯 PyAlembic 读取"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic.Abc import OArchive, IArchive
from alembic.AbcGeom import OPolyMesh, OPolyMeshSchemaSample, IPolyMesh, OXform
import imath
import tempfile

# === 创建一个测试 ABC (Ogawa 格式) ===
tmp = os.path.join(tempfile.gettempdir(), 'test_abc_reader.abc')
oar = OArchive(tmp, True)  # asOgawa=True
top = oar.getTop()

# 创建层级: group|cube (用 OXform 组)
grp = OXform(top, 'group')
mesh_obj = OPolyMesh(grp, 'cube')
schema = mesh_obj.getSchema()

positions = imath.V3fArray(8)
positions[0] = imath.V3f(-1, -1, -1)
positions[1] = imath.V3f( 1, -1, -1)
positions[2] = imath.V3f( 1,  1, -1)
positions[3] = imath.V3f(-1,  1, -1)
positions[4] = imath.V3f(-1, -1,  1)
positions[5] = imath.V3f( 1, -1,  1)
positions[6] = imath.V3f( 1,  1,  1)
positions[7] = imath.V3f(-1,  1,  1)

face_counts = imath.IntArray(6)
for i in range(6):
    face_counts[i] = 4

face_indices = imath.IntArray(24)
idx = [0,1,2,3, 4,5,6,7, 0,1,5,4, 2,3,7,6, 0,3,7,4, 1,2,6,5]
for i, v in enumerate(idx):
    face_indices[i] = v

sample = OPolyMeshSchemaSample(positions, face_indices, face_counts)
schema.set(sample)

# 显式释放所有 output 引用
del sample, schema, mesh_obj, grp, top, oar
print(f'Wrote: {tmp} ({os.path.getsize(tmp)} bytes)')

# 验证可读
print('Quick IArchive verify...')
iar = IArchive(tmp)
t = iar.getTop()
print(f'Top children: {t.getNumChildren()}')
del t, iar

# === 用 abc_reader 读 ===
from core.abc_reader import read_abc_as_info
info = read_abc_as_info(tmp)
print(f'Mesh count: {len(info["meshes"])}')
for dag, data in info['meshes'].items():
    vtx = data['vertices']
    mats = data['materials']
    uvs = {
        'u_array': len(data.get('u_array', [])),
        'v_array': len(data.get('v_array', [])),
        'uv_indices': len(data.get('uv_indices', [])),
    }
    pos_count = len(data['vert_positions'])
    print(f'  {dag}: {vtx} verts, {pos_count} floats, mats={mats}, uv={uvs}')

assert len(info['meshes']) == 1, f"Expected 1 mesh, got {len(info['meshes'])}"
mesh_data = list(info['meshes'].values())[0]
assert mesh_data['vertices'] == 8, f"Expected 8 verts, got {mesh_data['vertices']}"
assert len(mesh_data['vert_positions']) == 24, f"Expected 24 floats"
assert 'u_array' in mesh_data and 'v_array' in mesh_data and 'uv_indices' in mesh_data

print('SUCCESS - all assertions passed')
