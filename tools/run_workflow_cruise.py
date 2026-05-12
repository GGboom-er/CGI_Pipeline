"""一次性 workflow 巡航测试：走真实的 _submit_workflow → execute_workflow 路径，
用于验证模板引擎、segment 归属、context 渲染一整套端到端。

不同于 run_auto_cruise_test.py —— 那个是手工串 chain 一条一条跑（不经过 workflow 引擎）。
"""
import sys
import time
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.internals import _submit_workflow
from core.service_manager import shutdown_all as _shutdown_workers


def _audit_entries(task_id: str) -> list:
    """直接读 audit/{task_id}.json 的 JSONL。"""
    p = PROJECT_ROOT / 'audit' / f'{task_id}.json'
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out

X_RIG = r"X:\Project\ysj\pub\assets\chr\mihouwang\rig\rigMaster\ysj_chr_mihouwang_rig_rigMaster_v002.ma"
X_BLEND = r"X:\Project\ysj\pub\assets\chr\mihouwang\tex\texMaster\ysj_chr_mihouwang_tex_texMaster_v003.blend"

WORKFLOW_ID = 'tex_to_rig_verify_and_sync'


def poll(task_id: str, timeout: int = 0):
    t0 = time.time()
    last_ts = None
    while timeout <= 0 or time.time() - t0 < timeout:
        entries = _audit_entries(task_id) or []
        if entries:
            last = entries[-1]
            if last.get('ts') != last_ts:
                last_ts = last.get('ts')
                detail = (last.get('detail') or '')[:200]
                print(f"  [{last.get('status')}] {detail}")
            if last.get('status') in (
                'WORKFLOW_SUCCESS', 'WORKFLOW_ERROR',
                'WORKFLOW_ABORTED', 'WORKFLOW_AUDIT_FAILED', 'TIMEOUT',
            ):
                return last.get('status')
        time.sleep(3)
    return 'POLL_TIMEOUT'


def main():
    print("=" * 60)
    print(f"🚀 Workflow E2E：{WORKFLOW_ID}")
    print("=" * 60)

    # 重启 worker，保证新代码被加载
    print("\n🧹 重启所有 Worker（确保最新代码被加载）")
    _shutdown_workers()
    time.sleep(2)

    payload = {
        'workflow_id': WORKFLOW_ID,
        'project': 'ysj',
        'asset_name': 'mihouwang',
        'source_path': X_BLEND,
        'extra_params': {
            'rig_path': X_RIG,
        },
    }
    print(f"\n📨 提交 workflow...")
    print(f"   source_path = {X_BLEND}")
    print(f"   rig_path    = {X_RIG}")
    r = _submit_workflow(payload)
    print(f"   → {r}")
    task_id = r.get('task_id')
    if not task_id:
        print("❌ 提交失败")
        return 1

    print(f"\n⏳ 轮询 audit（不设硬超时）…")
    status = poll(task_id, timeout=0)
    print(f"\n🏁 最终状态: {status}")

    # 定位沙盒 & master 报告
    entries = _audit_entries(task_id) or []
    md_candidates = []
    for e in entries:
        if e.get('status') == 'FILE_STAGED':
            try:
                d = json.loads(e.get('detail', '{}'))
                if d.get('sandbox'):
                    md_candidates.append(Path(d['sandbox']).parent)
            except Exception:
                pass
    if md_candidates:
        run_dir = md_candidates[0]
        mds = list(run_dir.glob('*.md'))
        print(f"\n📁 沙盒: {run_dir}")
        print(f"   md 文件: {[p.name for p in mds]}")
        for p in mds:
            print(f"\n----- {p.name} -----")
            print(p.read_text(encoding='utf-8'))
    return 0 if status == 'WORKFLOW_SUCCESS' else 2


if __name__ == '__main__':
    sys.exit(main())
