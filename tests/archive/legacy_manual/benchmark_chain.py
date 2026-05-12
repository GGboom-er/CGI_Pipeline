"""
CGI Pipeline 四步链式批处理全流程计时脚本
通过 /api/execute_graph 提交到 Celery Worker 执行
"""
import requests, json, time, sys
from pathlib import Path

API = "http://localhost:9000"
AUDIT_DIR = Path(r"y:/GGbommer/scripts/CGI_Pipeline/audit")
SOURCE = "Y:/GGbommer/scripts/CGI_Pipeline/projects/ysj/pub/assets/chr/xiaotianquan/rig/rigMaster/ysj_chr_xiaotianquan_rig_rigMaster_v003.ma"
SAVE_PATH = "Y:/GGbommer/scripts/CGI_Pipeline/projects/ysj/runs/assets/chr/xiaotianquan/rig/rigMaster/ysj_chr_xiaotianquan_rig_rigMaster_v005.ma"

# Maya 代码：获取场景中所有 file 节点的 color 贴图路径
GET_TEXTURES_CODE = r"""
import maya.cmds as cmds
import json

file_nodes = cmds.ls(type='file') or []
result = []
for fn in file_nodes:
    ftp = cmds.getAttr(fn + '.fileTextureName') or ''
    if ftp:
        result.append({'node': fn, 'path': ftp})

# 输出 JSON
print('__RESULT_JSON__' + json.dumps({'texture_count': len(result), 'textures': result}, ensure_ascii=False))
"""

SKILL_CHAIN = [
    {"skill_id": "exec_code", "parameters": {"code": GET_TEXTURES_CODE, "description": "获取场景 color 贴图路径"}},
    {"skill_id": "clean_skinweights", "parameters": {"threshold": 0.001}},
    {"skill_id": "fix_shape_names", "parameters": {}},
    {"skill_id": "save_scene", "parameters": {"save_path": SAVE_PATH}},
]

STEP_NAMES = ["获取贴图路径", "清理微权重", "修复 Shape 命名", "保存文件"]

def main():
    print("=" * 60)
    print("CGI Pipeline 四步链式批处理")
    print("=" * 60)
    print(f"源文件: {SOURCE}")
    print(f"保存至: {SAVE_PATH}")
    print(f"技能链: {' → '.join(STEP_NAMES)}")
    print()

    # ── 阶段1: 提交整张图 ──
    t_submit = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] 🚀 提交技能链到 /api/execute_graph...")
    
    resp = requests.post(f"{API}/api/execute_graph", json={
        "source_path": SOURCE,
        "project": "ysj",
        "asset_name": "xiaotianquan",
        "skill_chain": SKILL_CHAIN,
    })
    data = resp.json()
    task_id = data.get("task_id", "?")
    dispatched = data.get("dispatched", False)
    t_dispatched = time.time()

    print(f"[{time.strftime('%H:%M:%S')}] ✓ 派发完成: task_id={task_id}, dispatched={dispatched}")
    print(f"  ⏱ 提交耗时: {t_dispatched - t_submit:.2f}s")
    print()

    if not dispatched:
        print("❌ 派发失败（Redis/Celery 未就绪）")
        return

    # ── 阶段2: 轮询审计日志等待结果 ──
    audit_file = AUDIT_DIR / f"{task_id}.json"
    print(f"[{time.strftime('%H:%M:%S')}] ⏳ 等待 Maya Worker 执行...")
    print(f"  审计日志: {audit_file}")
    print()

    last_step = -1
    step_times = {}
    t_execution_start = time.time()

    for tick in range(600):  # 最多等 10 分钟
        time.sleep(1)
        
        if not audit_file.exists():
            if tick % 10 == 0:
                print(f"  [{tick}s] 等待 Worker 接收任务...")
            continue

        lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
        
        # 解析最新进度
        for line in lines:
            entry = json.loads(line)
            status = entry.get("status", "")
            step = entry.get("step", -1)
            skill_id = entry.get("skill_id", "")
            ts = entry.get("ts", 0)

            if status == "CHAIN_STARTED" and "chain_start" not in step_times:
                step_times["chain_start"] = ts
                elapsed = ts - t_submit
                print(f"[{time.strftime('%H:%M:%S')}] ▶ 链开始执行 (Worker 接收延迟: {elapsed:.1f}s)")

            elif status == "STEP_START" and step > last_step:
                last_step = step
                step_times[f"step_{step}_start"] = ts
                name = STEP_NAMES[step] if step < len(STEP_NAMES) else skill_id
                print(f"[{time.strftime('%H:%M:%S')}] 🔄 [{step+1}/{len(SKILL_CHAIN)}] {name} 开始执行...")

            elif status.startswith("STEP_") and status != "STEP_START" and step == last_step:
                step_times[f"step_{step}_end"] = ts
                name = STEP_NAMES[step] if step < len(STEP_NAMES) else skill_id
                start_ts = step_times.get(f"step_{step}_start", ts)
                duration = ts - start_ts
                result_status = status.replace("STEP_", "")
                
                detail = entry.get("detail", "")[:150]
                symbol = "✓" if result_status == "SUCCESS" else "✗"
                print(f"[{time.strftime('%H:%M:%S')}] {symbol} [{step+1}/{len(SKILL_CHAIN)}] {name} → {result_status} ({duration:.1f}s)")
                if detail:
                    print(f"    {detail}")

        # 检查是否全部完成
        last_entry = json.loads(lines[-1])
        final_status = last_entry.get("status", "")
        if final_status in ("CHAIN_SUCCESS", "CHAIN_ABORTED", "CHAIN_ERROR", "TIMEOUT"):
            break
    else:
        print("❌ 超时（10分钟）")
        return

    t_done = time.time()

    # ── 阶段3: 汇总报告 ──
    print()
    print("=" * 60)
    print("📊 执行报告")
    print("=" * 60)
    
    total = t_done - t_submit
    dispatch_time = t_dispatched - t_submit
    worker_delay = step_times.get("chain_start", t_dispatched) - t_dispatched
    
    print(f"  总耗时:           {total:.1f}s")
    print(f"  API 提交:         {dispatch_time:.2f}s")
    print(f"  Worker 接收延迟:  {worker_delay:.1f}s")
    print()
    print(f"  {'步骤':<20} {'耗时':>8}  {'状态'}")
    print(f"  {'─'*20} {'─'*8}  {'─'*10}")
    
    for i, name in enumerate(STEP_NAMES):
        start = step_times.get(f"step_{i}_start", 0)
        end = step_times.get(f"step_{i}_end", 0)
        if start and end:
            print(f"  {name:<20} {end-start:>7.1f}s  ✓")
        elif start:
            print(f"  {name:<20}    未完成  ✗")
        else:
            print(f"  {name:<20}    未执行  -")

    print()
    print(f"  最终状态: {final_status}")

    # 输出贴图路径结果（如果有）
    for line in lines:
        entry = json.loads(line)
        if entry.get("skill_id") == "exec_code" and "detail" in entry:
            detail = entry["detail"]
            if "__RESULT_JSON__" in detail:
                idx = detail.index("__RESULT_JSON__") + len("__RESULT_JSON__")
                try:
                    tex_data = json.loads(detail[idx:])
                    print(f"\n  🎨 贴图路径 ({tex_data['texture_count']} 个):")
                    for t in tex_data.get("textures", [])[:10]:
                        print(f"    {t['node']}: {t['path']}")
                    if tex_data["texture_count"] > 10:
                        print(f"    ... 共 {tex_data['texture_count']} 个")
                except:
                    pass

if __name__ == "__main__":
    main()
