"""
⚠️  P0-A 时代一次性调试脚本（已过期）

读 `projects/ysj/20260505_191833_mihouwang_cruise_test/` 里的中间产物做
精度复核；用到的动作标签（SPATIAL_VOTING、REVIEW）是 P0-A 老术语。

保留用于翻查历史数据，不再维护。新测试走 `tests/test_rig_sync_profile.py`
与 `tests/test_pipeline_compare.py`。
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'vendor'))
import numpy as np
import alembic
from alembic import Abc, AbcGeom

sandbox = r'Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260505_191833_mihouwang_cruise_test'

abc_path = os.path.join(sandbox, 'ysj_chr_mihouwang_tex_texMaster_v003.abc')
archive = Abc.IArchive(abc_path)
abc_meshes = {}
def walk(obj):
    for i in range(obj.getNumChildren()):
        child = obj.getChild(i)
        try:
            mesh = AbcGeom.IPolyMesh(obj, child.getName())
            schema = mesh.getSchema()
            positions = schema.getPositionsProperty().samples[0]
            short_name = child.getName().replace('Shape', '')
            abc_meshes[short_name] = np.array(positions, dtype=np.float64)
        except:
            pass
        walk(child)
walk(archive.getTop())

post_path = os.path.join(sandbox, 'ysj_chr_mihouwang_rig_rigMaster_v002_post_sync.json')
with open(post_path, 'r', encoding='utf-8') as f:
    post_data = json.load(f)

sync_meshes = {}
mesh_data = post_data.get('meshes', post_data)
for key, info in mesh_data.items():
    positions = info.get('vert_positions') or info.get('vertices')
    if isinstance(positions, list) and len(positions) >= 3:
        verts = np.array(positions, dtype=np.float64).reshape(-1, 3)
        short_name = key.split('|')[-1].replace('Shape', '')
        sync_meshes[short_name] = verts

instr_path = os.path.join(sandbox, '_instructions_ysj_chr_mihouwang_tex_texMaster_v003_vs_ysj_chr_mihouwang_rig_rigMaster_v002_pre_sync.json')
with open(instr_path, 'r', encoding='utf-8') as f:
    instr = json.load(f)
voting_meshes = set()
review_meshes = set()
for key, val in instr.items():
    action = val.get('action', '')
    short_name = key.split('|')[-1].replace('Shape', '')
    if action == 'SPATIAL_VOTING':
        voting_meshes.add(short_name)
    elif action == 'REVIEW':
        review_meshes.add(short_name)

print(f'ABC meshes: {len(abc_meshes)}')
print(f'Sync meshes: {len(sync_meshes)}')
print(f'SPATIAL_VOTING: {sorted(voting_meshes)}')
print(f'REVIEW: {sorted(review_meshes)}')
print()

header = f"{'Mesh':<40} {'Verts':>6} {'MeanErr':>10} {'MaxErr':>10} {'Type':<12}"
print(header)
print('-' * 80)

results = []
for name in sorted(abc_meshes.keys()):
    if name not in sync_meshes:
        continue
    abc_v = abc_meshes[name] * 100.0  # ABC 是米制，Maya 是厘米制
    sync_v = sync_meshes[name]
    if len(abc_v) != len(sync_v):
        tag = 'VOTING' if name in voting_meshes else ('REVIEW' if name in review_meshes else 'IDENTICAL')
        print(f'{name:<40} abc={len(abc_v):>5} sync={len(sync_v):>5} TOPO_DIFF  {tag}')
        continue
    diffs = np.linalg.norm(abc_v - sync_v, axis=1)
    mean_d = np.mean(diffs)
    max_d = np.max(diffs)
    tag = 'VOTING' if name in voting_meshes else ('REVIEW' if name in review_meshes else 'IDENTICAL')
    results.append((name, len(abc_v), mean_d, max_d, tag))
    if max_d > 1e-5 or tag != 'IDENTICAL':
        print(f'{name:<40} {len(abc_v):>6} {mean_d:>10.6f} {max_d:>10.6f} {tag}')

print()
print('=== Summary ===')
for t in ['VOTING', 'REVIEW', 'IDENTICAL']:
    subset = [r for r in results if r[4] == t]
    if subset:
        avg_mean = np.mean([r[2] for r in subset])
        avg_max = np.mean([r[3] for r in subset])
        max_max = max(r[3] for r in subset)
        print(f'  {t} ({len(subset)} mesh): avg_mean={avg_mean:.6f}, avg_max={avg_max:.6f}, peak_max={max_max:.6f}')
