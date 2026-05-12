"""
⚠️  P0-A 时代一次性几何精度核验脚本（已过期）

对比 mihouwang 同步后的 rig mesh 与 ABC 真值的几何精度。
读取 post_sync.json 和 ABC，计算每个 SPATIAL_VOTING mesh 的顶点偏差。
SPATIAL_VOTING 是 P0-A 老动作标签，当前 compare 已改用 MODIFIED/MERGE/SPLIT/NEW。

保留用于翻查历史数据，不再维护。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor'))

import json
import numpy as np
import alembic
from alembic import Abc, AbcGeom

def read_abc_meshes(abc_path):
    """读取 ABC 中所有 mesh 的顶点数据"""
    archive = Abc.IArchive(abc_path)
    meshes = {}

    def walk(obj, path=""):
        for i in range(obj.getNumChildren()):
            child = obj.getChild(i)
            child_path = f"{path}|{child.getName()}" if path else child.getName()
            try:
                mesh = AbcGeom.IPolyMesh(obj, child.getName())
                schema = mesh.getSchema()
                positions = schema.getPositionsProperty().samples[0]
                verts = np.array(positions, dtype=np.float64)
                short_name = child.getName()
                meshes[short_name] = verts
            except Exception:
                pass
            walk(child, child_path)

    walk(archive.getTop())
    return meshes


def read_post_sync_meshes(json_path):
    """读取 post_sync.json 中的 mesh 顶点"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    meshes = {}
    for key, info in data.items():
        if 'vertices' in info and info['vertices']:
            verts = np.array(info['vertices'], dtype=np.float64).reshape(-1, 3)
            short_name = key.split('|')[-1].replace('Shape', '')
            meshes[short_name] = verts
    return meshes


def find_sandbox_dir():
    """找到最新的 mihouwang cruise test 沙盒"""
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'projects', 'ysj')
    dirs = sorted([d for d in os.listdir(base) if 'mihouwang_cruise_test' in d], reverse=True)
    if not dirs:
        print("未找到 mihouwang cruise test 沙盒")
        sys.exit(1)
    return os.path.join(base, dirs[0])


def main():
    sandbox = find_sandbox_dir()
    print(f"沙盒: {sandbox}")

    abc_path = None
    post_sync_path = None
    pairing_path = None

    for f in os.listdir(sandbox):
        if f.endswith('.abc'):
            abc_path = os.path.join(sandbox, f)
        if 'post_sync.json' in f and not f.startswith('_'):
            post_sync_path = os.path.join(sandbox, f)
        if f.startswith('_pairing') and 'pre_sync' in f:
            pairing_path = os.path.join(sandbox, f)

    if not abc_path or not post_sync_path:
        print(f"缺少文件: abc={abc_path}, post_sync={post_sync_path}")
        sys.exit(1)

    # 读 pairing_groups，把 PAIRED 组里的 abc mesh 标记为"走空间投射路径"（要做几何对比）
    voting_meshes = set()
    if pairing_path:
        with open(pairing_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        for g in payload.get('pairing_groups', []):
            if g.get('action') == 'PAIRED':
                for abc_dag in g.get('abc_dags', []):
                    short_name = abc_dag.split('|')[-1].replace('Shape', '')
                    voting_meshes.add(short_name)

    print(f"\nPAIRED 组 mesh: {sorted(voting_meshes)}")

    # 读取数据
    abc_meshes = read_abc_meshes(abc_path)
    sync_meshes = read_post_sync_meshes(post_sync_path)

    print(f"\nABC mesh 数量: {len(abc_meshes)}")
    print(f"Post-sync mesh 数量: {len(sync_meshes)}")

    # 对比
    print(f"\n{'─'*70}")
    print(f"{'Mesh':<35} {'顶点数':>6} {'平均偏差':>10} {'最大偏差':>10} {'类型':<12}")
    print(f"{'─'*70}")

    all_results = []
    for name in sorted(abc_meshes.keys()):
        if name not in sync_meshes:
            continue
        abc_v = abc_meshes[name]
        sync_v = sync_meshes[name]

        if len(abc_v) != len(sync_v):
            tag = "VOTING" if name in voting_meshes else "IDENTICAL"
            print(f"{name:<35} {len(abc_v):>6} {'拓扑不同':>10} {'N/A':>10} {tag:<12}")
            continue

        diffs = np.linalg.norm(abc_v - sync_v, axis=1)
        mean_diff = np.mean(diffs)
        max_diff = np.max(diffs)
        tag = "VOTING" if name in voting_meshes else "IDENTICAL"

        all_results.append((name, len(abc_v), mean_diff, max_diff, tag))

        # 只打印有差异的或 VOTING 的
        if max_diff > 1e-6 or name in voting_meshes:
            print(f"{name:<35} {len(abc_v):>6} {mean_diff:>10.6f} {max_diff:>10.6f} {tag:<12}")

    # 汇总
    voting_results = [r for r in all_results if r[4] == "VOTING"]
    identical_results = [r for r in all_results if r[4] == "IDENTICAL"]

    print(f"\n{'═'*70}")
    print("汇总:")
    if voting_results:
        avg_mean = np.mean([r[2] for r in voting_results])
        avg_max = np.mean([r[3] for r in voting_results])
        max_max = max(r[3] for r in voting_results)
        print(f"  SPATIAL_VOTING ({len(voting_results)} mesh):")
        print(f"    平均偏差均值: {avg_mean:.6f}")
        print(f"    最大偏差均值: {avg_max:.6f}")
        print(f"    最大偏差峰值: {max_max:.6f}")
    if identical_results:
        max_identical = max(r[3] for r in identical_results)
        print(f"  IDENTICAL ({len(identical_results)} mesh):")
        print(f"    最大偏差: {max_identical:.8f} (应为 ~0)")


if __name__ == '__main__':
    main()
