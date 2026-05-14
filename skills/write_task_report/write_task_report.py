# skills/write_task_report/write_task_report.py
# 任务汇总报告生成器。读 audit JSONL，按结构化规则渲染 md 到沙盒。
#
# 哲学：chain/workflow 引擎只往 audit 写事实，这个 skill 是"事实 → 人类报告"的单一呈现层。
# 改 md 格式只改这一个文件，不影响任何业务 skill。

import os
import time
import json
import datetime
from pathlib import Path

from core.bootstrap import cfg as _cfg
from core.receipt import make_receipt, _status_icon, _format_elapsed, _now_str

PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)


# ═══════════════════════════════════════════════════════════════
# audit 读取
# ═══════════════════════════════════════════════════════════════

def _read_audit(path: str) -> list:
    """按行读 JSONL。空行或坏行跳过。返回 entries 列表。"""
    p = Path(path)
    if not p.exists():
        return []
    entries = []
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            # 坏行包装成 fallback entry
            entries.append({
                'status': 'PARSE_ERROR',
                'skill_id': 'audit_parser',
                'detail': line[:500],
                'ts': 0,
                'step': -1,
            })
    return entries


def _extract_receipt(entry: dict) -> dict:
    """从一条 audit entry 里拆出 receipt 结构。
    entry.detail 是 receipt JSON 字符串（chain 已不再截断）。
    解析失败则返回降级 receipt。
    """
    detail = entry.get('detail', '')
    skill_id = entry.get('skill_id', 'unknown')
    status = entry.get('status', '')
    # 去尾部 " [mem=x.xxGB]"
    if isinstance(detail, str) and ' [mem=' in detail:
        detail = detail.rsplit(' [mem=', 1)[0]

    parsed = None
    if isinstance(detail, dict):
        parsed = detail
    elif isinstance(detail, str) and detail:
        try:
            parsed = json.loads(detail)
        except Exception:
            parsed = None
    if isinstance(parsed, dict) and ('skill_id' in parsed or 'outputs' in parsed):
        # 标准 receipt
        if 'status' not in parsed and status:
            parsed['status'] = status.replace('STEP_', '')
        parsed.setdefault('skill_id', skill_id)
        return parsed

    # 降级包装：detail 不是合法 JSON。
    # 如果看起来像半截 JSON（以 { 开头），标明"（detail 损坏或截断）"而非把 JSON 片段塞进 action
    looks_like_broken_json = (
        isinstance(detail, str) and detail.lstrip().startswith('{')
    )
    action_text = (
        '（detail 损坏或截断）' if looks_like_broken_json
        else ('（非标准输出）' if not detail else detail[:200])
    )
    # fallback 时把 skill_id 带进 summary，保留排障线索
    if skill_id and skill_id != 'unknown':
        action_text = f'`{skill_id}` — {action_text}'
    return {
        'skill_id': skill_id,
        'status': status.replace('STEP_', '') if status.startswith('STEP_') else status,
        'elapsed_min': 0,
        'summary': {'action': action_text},
        'items': [],
        'outputs': {},
        '_raw': detail[:2000] if isinstance(detail, str) else str(detail)[:2000],
    }


# ═══════════════════════════════════════════════════════════════
# 分派：识别 action 类型
# ═══════════════════════════════════════════════════════════════

# 约定：skill_id 前缀或精确匹配 → action 类型
# 多 category 走默认 default 渲染
_ACTION_BY_SKILL = {
    'copy_files': 'fetch',
    'pipeline_compare_asset': 'compare',
    'maya_sync_rig_incremental': 'edit',
    'maya_build_asset_info': 'collect',
    'blender_build_asset_info': 'collect',
    'maya_export_abc': 'convert',
    'blender_export_abc': 'convert',
    'maya_import_abc': 'convert',
    'blender_extract_materials': 'collect',
    'save_scene': 'save',
    'rename_asset': 'save',
    'maya_master_cleanup': 'edit',
    'maya_clean_skinweights': 'edit',
    'maya_fix_shape_names': 'edit',
    'maya_conform_normals': 'edit',
    'maya_freeze_transforms': 'edit',
    'maya_apply_materials': 'edit',
    'maya_assign_udim_materials': 'edit',
    'maya_build_mesh_from_abc': 'edit',
    'maya_check_textures': 'validate',
    'check_uvsets': 'validate',
    'simplify_uvsets': 'edit',
    'maya_compare_mesh_topology': 'compare',
    'validate_publish': 'validate',
    'maya_get_scene_info': 'collect',
    'maya_get_object_info': 'collect',
    'maya_capture_viewport': 'collect',
    'blender_capture_viewport': 'collect',
    'ping': 'default',
}


def _action_for(skill_id: str) -> str:
    if skill_id in _ACTION_BY_SKILL:
        return _ACTION_BY_SKILL[skill_id]
    if skill_id.endswith('_export_abc'):
        return 'convert'
    if skill_id.endswith('_build_asset_info') or skill_id.endswith('_get_scene_info'):
        return 'collect'
    if skill_id.startswith('compare') or '_compare' in skill_id:
        return 'compare'
    if skill_id.startswith('save_') or skill_id.startswith('rename_'):
        return 'save'
    if 'clean' in skill_id or 'cleanup' in skill_id or 'fix' in skill_id:
        return 'edit'
    return 'default'


_ACTION_LABELS = {
    'fetch': '📥 取文件',
    'open': '📂 开场景',
    'collect': '🔍 采集',
    'convert': '🔄 转换',
    'compare': '⚖️ 对比',
    'edit': '✏️ 编辑',
    'save': '💾 保存',
    'validate': '✅ 校验',
    'default': '▶ 执行',
}


# ═══════════════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════════════

def _escape_md(s) -> str:
    """md 反引号安全。"""
    if s is None:
        return ''
    s = str(s)
    if '`' in s:
        return s.replace('`', "'")
    return s


def _fmt_path(p) -> str:
    if not p:
        return ''
    return f'`{_escape_md(p)}`'


def _render_head(asset_name: str, task_id: str, source_path: str, project: str,
                 final_status: str, total_elapsed_min: float, steps_total: int) -> list:
    now = _now_str()
    lines = [
        f'# {asset_name} 任务报告',
        '',
        '| 字段 | 值 |',
        '|---|---|',
        f'| 任务 ID | `{task_id}` |',
        f'| 资产 | {asset_name} |',
    ]
    if project:
        lines.append(f'| 项目 | {project} |')
    if source_path:
        lines.append(f'| 来源文件 | {_fmt_path(source_path)} |')
    icon = _status_icon(final_status)
    lines.append(f'| 状态 | {icon} {final_status} |')
    lines.append(f'| 总耗时 | {_format_elapsed(total_elapsed_min)} |')
    lines.append(f'| 步骤数 | {steps_total} |')
    lines.append(f'| 生成时间 | {now} |')
    lines.append('')
    return lines


def _render_hold_banner(hold_skill_id: str, hold_step_idx: int, hold_detail: str,
                        hold_remaining: str, dcc_type: str = 'maya') -> list:
    lines = [
        '> ⚠️ **旧版链式执行暂停记录**',
        '>',
        f'> 暂停于 Step {hold_step_idx}: `{hold_skill_id}`',
        f'> 场景状态：历史记录来自 {dcc_type.capitalize()} 会话；当前后台 pipeline 不再依赖人工继续。',
        '',
        '### 问题详情',
        '',
        '```',
        (hold_detail or '')[:1500],
        '```',
        '',
    ]
    # 剩余步骤
    remaining = []
    if hold_remaining:
        try:
            remaining = json.loads(hold_remaining)
        except Exception:
            remaining = []
    if remaining:
        lines.append('### 未执行的剩余步骤')
        lines.append('')
        for s in remaining:
            sid = s.get('skill_id', '?')
            params = s.get('parameters', {})
            lines.append(f'- `{sid}` {params}')
        lines.append('')
    lines.append('### 可选操作')
    lines.append('')
    lines.append('1. **提交修复技能** — 场景还在 DCC 内存中，可以直接操作')
    lines.append('2. **继续执行剩余步骤** — 跳过当前问题')
    lines.append('3. **放弃** — 场景变更不保存')
    lines.append('')
    return lines


def _render_open_file(entries: list, step_counter: list) -> list:
    """把 CHAIN_FILE_OPENED / OPEN_FILE 渲染成单行摘要（不展开，省版面）。"""
    opened = [e for e in entries if e.get('status') in ('CHAIN_FILE_OPENED', 'OPEN_FILE')]
    if not opened:
        return []
    e = opened[0]
    src = e.get('detail', '')
    step_counter[0] += 1
    idx = step_counter[0]
    return [
        f'- **Step {idx}: 📂 开场景** — {_fmt_path(src) if src else "(内存)"} ✓',
        '',
    ]


def _parse_stage_detail(detail) -> dict:
    """FILE_STAGED 的 detail 是 JSON 字符串。容错解析。"""
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str) and detail:
        try:
            return json.loads(detail)
        except Exception:
            return {'origin': detail}
    return {}


def _render_workflow_context(entries: list, workflow_task_id: str) -> list:
    """渲染 workflow 入口的 Context 小节：
    - 源文件定位（WORKFLOW_STARTED / 来源路径）
    - FILE_STAGED：找服务器文件 + 备份到沙盒
    归属判定：entry.task_id == workflow_task_id（即 workflow 自己的事件，不是任何 seg 的）。
    """
    # 文件流转是调度事实，最终报告按 skill step 顺序阅读，避免额外可见折叠块打断主线。
    return []


def _render_segment_context(seg_stage_entries: list, seg_open_entry: dict) -> list:
    """段级上下文：只渲染"打开了哪个场景"。
    参数沙盒化已在 workflow 顶层 Context 统一展示，段级不再重复。
    seg_stage_entries 保留参数是为了兼容旧调用，当前被忽略。
    """
    lines = []
    if seg_open_entry:
        src = seg_open_entry.get('detail', '') or '-'
        lines.append(f'**📂 场景：** {_fmt_path(src)}')
        lines.append('')
    return lines


def _render_step(receipt: dict, step_idx: int) -> list:
    """Markdown step 结构：
    - 标题：Step N: {label} — {一句话动作} ({耗时}) {icon}
    - 正文：最多 4 行核心信息（动作 / 主输出 / 关键指标 / 错误）
    - items/完整 outputs/内嵌报告：在同一个 step 内用小标题和表格展示
    """
    skill_id = receipt.get('skill_id', 'unknown')
    status = receipt.get('status', 'UNKNOWN')
    icon = _status_icon(status)
    elapsed_min = receipt.get('elapsed_min', 0)
    elapsed_str = _format_elapsed(elapsed_min)
    summary = receipt.get('summary', {}) or {}
    action_desc = summary.get('action', '') or ''
    if summary.get('output_count'):
        action_desc += f' → {summary["output_count"]} 个 {summary.get("output_label", "项")}'

    action_type = _action_for(skill_id)
    label = _ACTION_LABELS.get(action_type, '▶ 执行')

    # 顶层一句话：优先用 action 描述；没描述时 fallback 到 skill_id（排障线索）
    summary_line = f'Step {step_idx}: {label}'
    if action_desc:
        summary_line += f' — {_escape_md(action_desc)}'
    else:
        summary_line += f' — `{skill_id}`'
    summary_line += f' ({elapsed_str}) {icon}'

    lines = [
        f'### {summary_line}',
        '',
    ]

    # 核心字段（2-4 行，不再重复动作/耗时/状态——它们已在 summary 行里）
    outputs = receipt.get('outputs', {}) or {}
    key_lines = []
    if outputs.get('output_path'):
        key_lines.append(f'- **输出**: {_fmt_path(outputs["output_path"])}')
    elif outputs.get('report_path'):
        key_lines.append(f'- **报告**: {_fmt_path(outputs["report_path"])}')
    # 指标：只收标量（int/float/str）；dict/list 归到二级 details
    _skip = {'output_path', 'report_path', 'report_content',
             'entries_count', 'units_rendered', 'mode', 'final_status'}
    _scalar_metrics = []
    _complex_metrics = {}
    for k, v in outputs.items():
        if k in _skip:
            continue
        if isinstance(v, (dict, list)):
            _complex_metrics[k] = v
        else:
            _scalar_metrics.append(f'{k}={_escape_md(v)}')
    if _scalar_metrics:
        key_lines.append(f'- **指标**: {" · ".join(_scalar_metrics[:8])}')
    err = receipt.get('error', '')
    if err:
        key_lines.append(f'- **❌ 错误**: {_escape_md(err)[:300]}')
        if receipt.get('recovery_hint'):
            key_lines.append(f'- **恢复建议**: {_escape_md(receipt["recovery_hint"])[:200]}')

    if key_lines:
        lines.extend(key_lines)
        lines.append('')

    # 复杂 outputs：不再二级折叠，避免 Markdown 查看器的嵌套 details 兼容问题。
    if _complex_metrics:
        lines.append('#### 详细指标')
        lines.append('')
        lines.append('| 字段 | 值 |')
        lines.append('|---|---|')
        for k, v in _complex_metrics.items():
            s = json.dumps(v, ensure_ascii=False)
            if len(s) > 300:
                s = s[:297] + '...'
            lines.append(f'| {_escape_md(k)} | `{_escape_md(s)}` |')
        lines.append('')

    # items 详情
    items = receipt.get('items', []) or []
    if items:
        lines.append(f'#### 受影响对象明细（共 {len(items)} 项）')
        lines.append('')
        lines.append('| 对象 | 详情 | 耗时 |')
        lines.append('|---|---|---|')
        for it in items[:30]:
            name = _escape_md(it.get('name', ''))
            detail = _escape_md(it.get('detail', ''))[:200]
            em = it.get('elapsed_min')
            t = _format_elapsed(em) if em is not None and em >= 0 else '-'
            lines.append(f'| {name} | {detail} | {t} |')
        if len(items) > 30:
            lines.append(f'| ... | 共 {len(items)} 项，仅显示前 30 | - |')
        lines.append('')

    # 内嵌报告（如 pipeline_compare_asset 的 MD）
    report_content = receipt.get('report_content', '') or outputs.get('report_content', '')
    if report_content:
        lines.append('#### 完整对比报告')
        lines.append('')
        lines.append(report_content)
        lines.append('')

    # _raw（fallback）
    if '_raw' in receipt and not items and not err:
        lines.append('#### 原始 detail（解析失败）')
        lines.append('')
        lines.append('```')
        lines.append(receipt['_raw'])
        lines.append('```')
        lines.append('')

    lines.append('')
    return lines


def _infer_final_status(entries: list) -> str:
    """从 audit entries 末尾推断任务最终状态。"""
    for e in reversed(entries):
        s = e.get('status', '')
        if s in ('CHAIN_SUCCESS', 'WORKFLOW_SUCCESS', 'SUCCESS'):
            return 'SUCCESS'
        if s == 'CHAIN_HELD':
            return 'ERROR'
        if s == 'CHAIN_BLOCKED':
            return 'BLOCKED'
        if s in ('AUDIT_FAILED', 'STEP_AUDIT_FAILED', 'CHAIN_AUDIT_FAILED', 'WORKFLOW_AUDIT_FAILED'):
            return 'AUDIT_FAILED'
        if s in ('CHAIN_ABORTED', 'WORKFLOW_ABORTED'):
            return 'ERROR'
        if s in ('CHAIN_ERROR', 'WORKFLOW_ERROR', 'TIMEOUT'):
            return 'ERROR'
    return 'UNKNOWN'


def _total_elapsed(entries: list) -> float:
    """用 audit 头尾时间戳算总耗时（分钟）。"""
    ts = [e.get('ts') for e in entries if isinstance(e.get('ts'), (int, float)) and e.get('ts')]
    if len(ts) < 2:
        return 0
    return round((max(ts) - min(ts)) / 60, 2)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def _resolve_run_dir(task_id: str, given: str) -> Path:
    """run_dir 优先级：显式传 > Redis > runs/{task_id} 兜底。"""
    if given:
        d = Path(given)
        d.mkdir(parents=True, exist_ok=True)
        return d
    try:
        from core.service_manager import is_redis_alive
        import redis
        if is_redis_alive():
            r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
            cached = r.hget(f'wf:{task_id}:state', 'sandbox_dir')
            if cached:
                d = Path(cached)
                d.mkdir(parents=True, exist_ok=True)
                return d
    except Exception as e:
        redis_fallback_reason = str(e)
    fallback = PROJECT_ROOT / 'runs' / task_id
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _render_segment(rc: dict, seg_idx: int, step_entries: list = None,
                    seg_stage_entries: list = None, seg_open_entry: dict = None) -> list:
    """渲染 workflow 的一个 segment 块。rc 是从 SEGMENT_* entry 解析出的 receipt-like 结构。

    step_entries: 归属本段的 STEP_* audit 条目（非 STEP_START），用于展开步骤明细。
    seg_stage_entries: 归属本段的 FILE_STAGED 条目。
    seg_open_entry:   归属本段的 CHAIN_FILE_OPENED/OPEN_FILE 条目。
    """
    status = rc.get('status', '')
    icon = _status_icon(status)
    dcc = rc.get('dcc', '')
    n_steps = rc.get('step_count', 0)
    elapsed_min = float(rc.get('elapsed_min', 0))
    summary = rc.get('summary', {}) or {}
    action = summary.get('action', '') or (f'{dcc} · {n_steps} 步' if dcc else '')

    head = f'Segment {seg_idx}: 🎬 {action} ({_format_elapsed(elapsed_min)}) {icon}'
    lines = [
        f'## {head}',
        '',
    ]
    if dcc:
        lines.append(f'**DCC：** {dcc}')
        lines.append('')

    # 段内上下文（参数沙盒化 + 开场景）
    lines.extend(_render_segment_context(seg_stage_entries or [], seg_open_entry))

    err = rc.get('error', '')
    if err:
        lines.append(f'**❌ 错误：** {_escape_md(str(err)[:500])}')
        lines.append('')

    # 展开段内步骤明细
    if step_entries:
        local_step_counter = [0]
        for e in step_entries:
            sub_rc = _extract_receipt(e)
            local_step_counter[0] += 1
            lines.extend(_render_step(sub_rc, local_step_counter[0]))

    lines.append('')
    return lines


def _extract_segment(entry: dict) -> dict:
    """从 SEGMENT_* audit entry 拆出 segment 信息。detail 可能是 JSON 或字符串。"""
    detail = entry.get('detail', '')
    status_full = entry.get('status', '')
    status = status_full.replace('SEGMENT_', '') if status_full.startswith('SEGMENT_') else status_full

    parsed = None
    if isinstance(detail, dict):
        parsed = detail
    elif isinstance(detail, str) and detail:
        try:
            parsed = json.loads(detail)
        except Exception:
            parsed = None

    if isinstance(parsed, dict):
        parsed.setdefault('status', status)
        return parsed

    return {
        'status': status,
        'dcc': '',
        'step_count': 0,
        'elapsed_min': 0,
        'summary': {'action': str(detail)[:200] if detail else ''},
        'outputs': {},
    }


def execute(payload: dict) -> dict:
    t0 = time.time()
    params = payload.get('parameters', {}) or {}

    task_id = params.get('task_id') or payload.get('task_id') or ''
    if not task_id:
        return make_receipt('write_task_report', 'ERROR', t0,
                            error='缺少 task_id')
    asset_name = payload.get('asset_name', 'untitled')
    project = payload.get('project', '')
    mode = params.get('mode', 'normal')
    source_path = params.get('source_path', '') or payload.get('source_path', '')

    audit_path = params.get('audit_path') or str(PROJECT_ROOT / 'audit' / f'{task_id}.json')
    run_dir = _resolve_run_dir(task_id, params.get('run_dir', ''))

    entries = _read_audit(audit_path)
    # 任务级状态与耗时。hold 模式仅兼容旧审计，不再强制 NEEDS_ATTENTION。
    final_status = _infer_final_status(entries)
    total_min = _total_elapsed(entries)

    # mode='workflow' 走 SEGMENT_* 路径；其它（normal/hold）走 STEP_* 路径
    if mode == 'workflow':
        segment_entries = [
            e for e in entries
            if str(e.get('status', '')).startswith('SEGMENT_')
            and e.get('status') != 'SEGMENT_START'
            and e.get('status') != 'SEGMENT_SKIPPED'
        ]
        # 收集所有 STEP_* (非 STEP_START)，后面按 task_id 归段
        all_step_entries = [
            e for e in entries
            if str(e.get('status', '')).startswith('STEP_')
            and e.get('status') != 'STEP_START'
        ]
        # 收集 FILE_STAGED / CHAIN_FILE_OPENED 等上下文事件，后面按 task_id 归段或归 workflow
        all_stage_entries = [e for e in entries if e.get('status') == 'FILE_STAGED']
        all_open_entries = [
            e for e in entries
            if e.get('status') in ('CHAIN_FILE_OPENED', 'OPEN_FILE')
        ]
        step_entries = []
    else:
        step_entries = [
            e for e in entries
            if str(e.get('status', '')).startswith('STEP_')
            and e.get('status') != 'STEP_START'
        ]
        segment_entries = []
        all_step_entries = []
        all_stage_entries = []
        all_open_entries = []

    # 头部
    units_total = len(segment_entries) if mode == 'workflow' else len(step_entries)
    lines = _render_head(asset_name, task_id, source_path, project,
                         final_status, total_min, units_total)

    # hold 警告块
    if mode == 'hold':
        lines.extend(_render_hold_banner(
            params.get('hold_skill_id', ''),
            int(params.get('hold_step_idx', -1)),
            params.get('hold_detail', ''),
            params.get('hold_remaining', ''),
        ))

    # workflow 级上下文（源文件定位 / 沙盒备份）
    if mode == 'workflow':
        lines.extend(_render_workflow_context(entries, task_id))

    # 开文件 step（非技能，但值得记录）
    step_counter = [0]
    if mode != 'workflow':
        lines.extend(_render_open_file(entries, step_counter))

    # 渲染主体
    units_rendered = 0
    if mode == 'workflow':
        for idx, e in enumerate(segment_entries):
            seg = _extract_segment(e)
            seg_task_id = f'{task_id}_seg{idx}'
            seg_steps = [s for s in all_step_entries if s.get('task_id') == seg_task_id]
            seg_stages = [s for s in all_stage_entries if s.get('task_id') == seg_task_id]
            seg_opens = [s for s in all_open_entries if s.get('task_id') == seg_task_id]
            seg_open = seg_opens[0] if seg_opens else None
            lines.extend(_render_segment(seg, idx, seg_steps, seg_stages, seg_open))
            units_rendered += 1
    else:
        for e in step_entries:
            rc = _extract_receipt(e)
            step_counter[0] += 1
            lines.extend(_render_step(rc, step_counter[0]))
            units_rendered += 1

    # 结尾签名
    lines.append('---')
    lines.append(f'*CGI Pipeline · {_now_str()}*')
    lines.append('')

    # 写沙盒。运行时唯一报告固定为 REPORT.md；本 skill 作为 audit 重建工具时也覆盖同一文件。
    out_path = run_dir / 'REPORT.md'
    out_path.write_text('\n'.join(lines), encoding='utf-8')

    return make_receipt(
        skill_id='write_task_report',
        status='SUCCESS',
        start_time=t0,
        summary_input=audit_path,
        summary_action=f'→ {out_path.name}',
        summary_count=units_rendered,
        summary_label='段' if mode == 'workflow' else '步骤',
        outputs={
            'output_path': str(out_path),
            'result': {
                'entries_count': len(entries),
                'units_rendered': units_rendered,
                'mode': mode,
                'final_status': final_status,
            },
        },
    )
