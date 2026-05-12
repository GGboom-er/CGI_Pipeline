import sys
sys.path.insert(0, r'Y:\GGbommer\scripts\CGI_Pipeline')
from core.abc_reader import read_abc_as_info

abc_path = r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260503_235714_mihouwang_cruise_test\tex.abc'
tex_info = read_abc_as_info(abc_path)

dag = 'ABC|Group|cache|mihouwang_hiddenMesh_Grp|mihouwang_body1_hairbasemesh|mihouwang_body1_hairbasemeshShape'
mesh_data = tex_info['meshes'].get(dag)

if mesh_data:
    print('num_v:', mesh_data.get('vertices'))
    print('pos len:', len(mesh_data.get('vert_positions', [])))
    print('fc len:', len(mesh_data.get('face_counts', [])))
    print('fi len:', len(mesh_data.get('face_indices', [])))
else:
    print('Mesh not found in abc!')
