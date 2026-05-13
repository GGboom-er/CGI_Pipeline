# core/tasks.py
# ── CGI Pipeline v2.0 — 每链一进程架构 ──
#
# 流程：AI 指令 → MCP → Celery → Worker(新进程) → 执行技能链 → 退出
#
# Worker 生命周期：每个 Celery 任务创建新 DCC 进程，执行完 shutdown。
# 路径系统：统一使用 AssetResolver（基于项目配置）。

from celery import Celery
from celery.utils.log import get_task_logger
import json, time, traceback, os, shutil
from pathlib import Path

from core.bootstrap import cfg as _cfg

PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)

import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from core.receipt import get_report_path
from core.skill_registry import get_skill_dcc, get_skip_audit_skills, reload as _reload_skill_registry
from core.progress import (
    publish_event, persist_outputs, restore_outputs,
    mark_segment_done, get_completed_segments, cleanup_workflow_state,
)
from core.run_archive import (
    create_run_dir, get_run_report_path, write_manifest,
    copy_to_run_dir, cleanup_old_runs, is_report_path,
    cleanup_root_machine_duplicates,
)
from core import task_report_writer as _report_writer

app = Celery('cgi_pipeline')
app.config_from_object('config.celeryconfig')
logger = get_task_logger(__name__)

# ── 不需要走质检/发布的技能白名单（从动态 skill_registry 读取）──
_SKIP_AUDIT_SKILLS = get_skip_audit_skills()


def _get_audit_path(task_id: str) -> Path:
    """返回审计日志路径。统一存放在全局 audit 目录下供 MCP 和 Dashboard 查询。"""
    audit_dir = PROJECT_ROOT / 'audit'
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir / f'{task_id}.json'


def _create_worker(dcc_type: str = 'maya', source_path: str = None):
    """创建新的 DCC Worker 实例（每次调用都是新进程）"""
    from core.dcc_factory import create_worker
    return create_worker(dcc_type, source_path)


def _get_resolver(project: str):
    """根据项目名创建 AssetResolver"""
    from core.config_loader import load_project_config
    from core.asset_resolver import AssetResolver
    cfg = load_project_config(project)
    return AssetResolver(cfg)


def _open_source_file(worker, task_id: str, source_path: str, dcc_type: str):
    """在 DCC Worker 中打开源文件，链式和单技能共用。返回 (ok, error_msg)。"""
    from core.dcc_factory import open_source_file
    return open_source_file(worker, task_id, source_path, dcc_type)


# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# 统一报告辅助
# ═══════════════════════════════════════════════════════════════


def _call_write_report_for_chain(task_id, asset_name, project, source_path,
                                  run_dir, audit_path, mode='normal',
                                  hold_info=None):
    """chain / workflow / single_skill 收尾统一走 write_task_report skill。

    mode: 'normal' | 'workflow'
    hold_info 仅保留旧审计兼容；后台 pipeline 不再进入人工暂停流程。
    """
    try:
        from skills.write_task_report.write_task_report import execute as _write_report_exec
        params = {
            'task_id': task_id,
            'audit_path': str(audit_path),
            'run_dir': str(run_dir),
            'mode': mode,
            'source_path': source_path,
        }
        if hold_info:
            params.update({
                'hold_skill_id': hold_info.get('skill_id', ''),
                'hold_step_idx': hold_info.get('step_idx', -1),
                'hold_detail': hold_info.get('detail', ''),
                'hold_remaining': json.dumps(hold_info.get('remaining_steps') or [],
                                             ensure_ascii=False),
            })
        result = _write_report_exec({
            'task_id': task_id,
            'asset_name': asset_name,
            'project': project,
            'source_path': source_path,
            'parameters': params,
        })
        outs = result.get('outputs', {}) if isinstance(result, dict) else {}
        return outs.get('output_path', '')
    except Exception as e:
        logger.warning(f'[{task_id}] write_task_report 调用失败: {e}', exc_info=True)
        return ''


def _update_progress(task_instance, phase: str, message: str, step_info: str = ''):
    """辅助函数：向 Redis 发送实时状态进度"""
    if task_instance and hasattr(task_instance, 'update_state'):
        if not getattr(task_instance.request, 'id', None):
            return  # 如果是同步调用导致 request.id 为空，则跳过进度更新
        meta = {
            'phase': phase,
            'message': message,
            'step_info': step_info,
            'timestamp': time.time()
        }
        task_instance.update_state(state='PROGRESS', meta=meta)


# ═══════════════════════════════════════════════════════════════
# 链式执行 — 单 DCC 会话顺序执行技能链
# ═══════════════════════════════════════════════════════════════

@app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=5,
    name='core.tasks.execute_skill_chain'
)
def execute_skill_chain(self, payload: dict):
    """
    链式执行：创建新 DCC 进程，顺序执行多个技能，完成后退出。

    payload = {
        'task_id': 'chain-xxx',
        'source_path': '...',
        'project': 'ysj',
        'asset_name': 'xiaotianquan',
        'skill_chain': [
            {'skill_id': 'clean_skinweights', 'parameters': {'threshold': 0.001}},
            {'skill_id': 'save_scene', 'parameters': {'save_path': '...'}},
        ]
    }
    """
    task_id = payload.get('task_id', self.request.id)
    source_path = payload.get('source_path', '')
    skill_chain = payload.get('skill_chain', [])
    asset_name = payload.get('asset_name', 'untitled')
    project = payload.get('project', 'default')
    workflow_id = payload.get('workflow_id', task_id)
    audit_path = _get_audit_path(workflow_id)
    # 作为 workflow 子段运行时，不写独立 md（master 由 workflow 统一渲染）
    is_subchain = workflow_id != task_id
    # 外层编排可显式关闭链级报告，只保留审计给最终总报告消费。
    suppress_report = bool(payload.get('suppress_report', False))

    # ── 沙盒目录（所有出口的报告都落这儿）──
    external_run_dir = payload.get('run_dir')
    if external_run_dir:
        run_dir = Path(external_run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = create_run_dir(task_id, project, asset_name, payload.get('submitted_at'))
    info_dir = run_dir / '.info'
    info_dir.mkdir(parents=True, exist_ok=True)

    # ── 运行时唯一报告 ──
    report_path = '' if suppress_report else (
        payload.get('report_path') or _report_writer.get_report_path(run_dir)
    )
    report_context = {
        'task_id': workflow_id if is_subchain else task_id,
        'asset_name': asset_name,
        'project': project,
        'source_path': source_path,
        'run_dir': str(run_dir),
        'workflow_id': workflow_id if is_subchain else payload.get('workflow_id', ''),
    }
    if report_path and (not is_subchain or not Path(report_path).exists()):
        _report_writer.init_report(report_path, report_context)
    _t_chain_start = time.time()
    staged_records = []

    def _write_audit(status, detail='', step_idx=-1, skill_id=''):
        entry = {
            'task_id': task_id, 'skill_id': skill_id or 'chain',
            'status': status, 'ts': time.time(), 'detail': detail,
            'step': step_idx, 'total_steps': len(skill_chain),
        }
        with open(audit_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')

    def _translate_error(detail: str, skill_id: str) -> str:
        if "No such file or directory" in detail or "FileNotFoundError" in detail or "文件不存在或为空" in detail:
            return f"[文件读取异常] 尝试读取的源文件不存在或被占用，请检查上游环节是否已正确发布。原生报错: {detail[:200]}"
        if "IndexError" in detail and "list index out of range" in detail:
            return f"[索引越界] {skill_id} 技能执行时遇到数组越界，场景中可能缺失预期的节点或组件。原生报错: {detail[:200]}"
        if "KeyError" in detail:
            return f"[数据缺失] 缺少关键数据键值。原生报错: {detail[:200]}"
        if "RuntimeError" in detail and "Object does not exist" in detail:
            return f"[节点丢失] 场景中找不到指定的节点，可能被篡改或删除。原生报错: {detail[:200]}"
        return detail

    def _record_file_staged(record: dict):
        staged_records.append(record)
        _write_audit('FILE_STAGED', json.dumps(record, ensure_ascii=False))
        if report_path:
            _report_writer.upsert_file_staged(report_path, staged_records)

    def _finalize_runtime_report(status: str, error: str = '', tb: str = ''):
        if report_path:
            elapsed_min = (time.time() - _t_chain_start) / 60
            ctx = dict(report_context)
            ctx['source_path'] = source_path
            _report_writer.finalize_report(
                report_path, ctx, status, elapsed_min=elapsed_min,
                error=error, traceback_text=tb,
            )

    worker = None
    try:
        _write_audit('CHAIN_STARTED', f'{len(skill_chain)} skills')

        # 热重载注册表，避免 Worker 长驻时新增技能无法识别
        _reload_skill_registry()

        dcc_type = get_skill_dcc(
            skill_chain[0]['skill_id'] if skill_chain else 'ping'
        )

        # 校验链内所有技能属于同一 DCC
        dcc_types_in_chain = set(
            get_skill_dcc(s['skill_id']) for s in skill_chain
        )
        if len(dcc_types_in_chain) > 1:
            err_msg = f'链中混合了多个 DCC 类型: {dcc_types_in_chain}'
            _write_audit('CHAIN_ABORTED', err_msg)
            _finalize_runtime_report('CHAIN_ABORTED', error=err_msg)
            return {
                'task_id': task_id, 'status': 'CHAIN_ABORTED',
                'error': f'链中所有技能必须属于同一个 DCC，当前包含: {dcc_types_in_chain}',
                'chain_results': [], 'report_path': report_path,
            }

        _update_progress(self, 'STARTING_WORKER', f'正在启动 {dcc_type.capitalize()} 后台进程')
        worker = _create_worker(dcc_type, source_path)
        chain_results = []

        # 链引擎统一打开文件或清空场景（无条件沙盒化）
        open_elapsed_sec = 0
        import shutil

        def _stage_to_sandbox(src: str, role: str = 'param') -> str:
            """无条件将文件拷贝到沙盒。所有链式执行都在沙盒内闭环操作。"""
            if not src or not isinstance(src, str): return src
            if not (src.endswith('.ma') or src.endswith('.mb') or src.endswith('.blend') or src.endswith('.json') or src.endswith('.abc')): return src
            src_path = Path(src)
            if not src_path.exists(): return src
            # 已经在本次 run_dir 内的文件无需重复拷贝
            try:
                src_path.resolve().relative_to(run_dir.resolve())
                _record_file_staged({
                    'role': role, 'origin': str(src_path), 'sandbox': str(src_path),
                    'skipped': True, 'reason': 'already_in_sandbox', 'elapsed_sec': 0.0,
                })
                return src
            except ValueError:
                pass
            dst_path = run_dir / src_path.name
            _t_copy = time.time()
            reused = dst_path.exists()
            if not dst_path.exists():
                shutil.copy2(str(src_path), str(dst_path))
                logger.info(f'[{task_id}] 沙盒化: {src_path.name} → {dst_path}')
            _record_file_staged({
                'role': role, 'origin': str(src_path), 'sandbox': str(dst_path),
                'reused': reused, 'elapsed_sec': time.time() - _t_copy,
            })
            return str(dst_path)

        # 扫描并沙盒化 skill_chain 中的所有文件参数
        for step in skill_chain:
            params = step.get('parameters', {})
            for k, v in params.items():
                if isinstance(v, str):
                    params[k] = _stage_to_sandbox(v, role=f'step.{step.get("skill_id", "?")}.{k}')

        if source_path and dcc_type != 'pipeline':
            # 无条件沙盒化主场景文件（X盘额外有只读锁，但所有路径都应在沙盒内操作）
            src_p = Path(source_path)
            try:
                src_p.resolve().relative_to(run_dir.resolve())
                # 已在 run_dir 内，无需拷贝
            except ValueError:
                try:
                    local_path = str(run_dir / os.path.basename(source_path))
                    reused = os.path.exists(local_path)
                    _t_copy = time.time()
                    if not reused:
                        shutil.copy2(source_path, local_path)
                    logger.info(f'[{task_id}] 沙盒化主场景: {source_path} → {local_path}')
                    _record_file_staged({
                        'role': 'source_path', 'origin': source_path, 'sandbox': local_path,
                        'reused': reused, 'elapsed_sec': time.time() - _t_copy,
                    })
                    source_path = local_path
                except Exception as e:
                    logger.warning(f'[{task_id}] 沙盒化主场景失败，使用原路径: {e}')
            _write_audit('CHAIN_OPEN_FILE', source_path, -1, 'open_file')
            if report_path:
                _report_writer.upsert_open_scene(report_path, source_path, 'RUNNING')
            _update_progress(self, 'OPENING_FILE', f'正在打开源文件: {source_path}')
            _t_open = time.time()
            ok, err = _open_source_file(worker, task_id, source_path, dcc_type)
            open_elapsed_sec = time.time() - _t_open
            if not ok:
                meaningful_err = _translate_error(err, "open_file")
                _write_audit('CHAIN_ABORTED', f'打开文件失败: {meaningful_err}')
                if report_path:
                    _report_writer.upsert_open_scene(
                        report_path, source_path, 'ERROR',
                        elapsed_sec=open_elapsed_sec, error=meaningful_err,
                    )
                _finalize_runtime_report('CHAIN_ABORTED', error=meaningful_err)
                return {
                    'task_id': task_id, 'status': 'CHAIN_ABORTED',
                    'failed_step': -1, 'error': f'打开文件失败: {source_path}',
                    'chain_results': [], 'report_path': report_path,
                }
            _write_audit('CHAIN_FILE_OPENED', source_path, -1, 'open_file')
            if report_path:
                _report_writer.upsert_open_scene(
                    report_path, source_path, 'SUCCESS', elapsed_sec=open_elapsed_sec
                )
        elif not source_path and dcc_type in ('maya', 'blender'):
            # 对于 Warm Pool，如果没有源文件，必须强制清空场景防止污染
            _open_source_file(worker, task_id, "", dcc_type)

        # 从 payload 中提取 workflow_id（由 execute_workflow 注入）
        _wf_id = payload.get('workflow_id', '')
        _seg_idx = payload.get('segment_index', -1)

        # 链内模板解析上下文（跨 segment 的 outputs 由 workflow 注入，chain 内逐步累加）
        _chain_outputs = dict(payload.get('_chain_outputs_in', {}) or {})
        _chain_extra_params = payload.get('extra_params', {}) or {}
        _chain_config = payload.get('_chain_config', {}) or {}
        _chain_extra_params.setdefault('run_dir', str(run_dir))
        _chain_extra_params.setdefault('info_dir', str(info_dir))

        for i, step in enumerate(skill_chain):
            step_skill_id = step['skill_id']
            step_params_raw = step.get('parameters', {})
            # 每一步执行前按当前已完成 step 的 outputs 解析模板
            try:
                step_params = _resolve_template_vars(
                    step_params_raw, _chain_outputs, _chain_extra_params, _chain_config
                )
            except Exception as _ex:
                logger.warning(f'[{task_id}] step {i} 模板解析失败: {_ex}')
                step_params = dict(step_params_raw)
            step_task_id = f'{task_id}_s{i}'

            if step_skill_id == 'save_scene' and chain_results:
                step_params = dict(step_params)
                step_params['_chain_history'] = chain_results
                step_params['_open_elapsed_sec'] = open_elapsed_sec

            step_payload = {
                'task_id': step_task_id,
                'skill_id': step_skill_id,
                'source_path': source_path,
                'project': payload.get('project', 'default'),
                'asset_name': payload.get('asset_name', 'untitled'),
                'parameters': step_params,
                'run_dir': str(run_dir),
                'info_dir': str(info_dir),
                'extra_params': dict(_chain_extra_params),
            }

            _write_audit('STEP_START', f'step {i}: {step_skill_id}', i, step_skill_id)
            step_report_context = {
                'step_index': i,
                'step_total': len(skill_chain),
                'skill_id': step_skill_id,
                'parameters': step_params,
                'source_path': source_path,
                'segment': _seg_idx,
            }
            if report_path:
                _report_writer.upsert_step_started(report_path, step_report_context)
            _update_progress(self, 'EXECUTING_SKILL', f'正在执行技能: {step_skill_id}', f'{i+1}/{len(skill_chain)}')

            # 进度推送：步骤开始
            if _wf_id:
                publish_event(_wf_id, {
                    'event_type': 'step.start',
                    'segment': _seg_idx,
                    'step': i,
                    'step_total': len(skill_chain),
                    'skill_id': step_skill_id,
                    'progress': i / len(skill_chain),
                    'message': f'正在执行: {step_skill_id} ({i+1}/{len(skill_chain)})',
                })
            result = worker.run_skill(step_payload)
            step_status = result.get('status', 'UNKNOWN')

            raw_detail = result.get('detail', '')
            if not raw_detail and isinstance(result, dict) and (
                'summary' in result or 'outputs' in result or 'skill_id' in result
            ):
                raw_detail = json.dumps(result, ensure_ascii=False, default=str)

            if step_status == 'ERROR':
                translated_err = _translate_error(str(raw_detail), step_skill_id)
                raw_detail = translated_err
                result['detail'] = translated_err

            receipt = _report_writer.extract_receipt(raw_detail or result, step_skill_id, step_status)
            if step_status != 'SUCCESS' and receipt.get('status') == 'SUCCESS':
                receipt['status'] = step_status

            mem_gb = worker.get_memory_gb() if hasattr(worker, 'get_memory_gb') else -1
            step_result = {
                'step': i, 'skill_id': step_skill_id,
                'status': step_status, 'detail': raw_detail,
                'memory_gb': round(mem_gb, 2),
            }
            chain_results.append(step_result)
            # audit detail 不截断：audit JSONL 是报告的单一事实源，
            # 截断会破坏 receipt JSON 结构导致 write_task_report 渲染 fallback。
            _write_audit(
                f'STEP_{step_status}',
                str(raw_detail) + f' [mem={mem_gb:.2f}GB]',
                i, step_skill_id,
            )
            if report_path:
                _report_writer.upsert_step_finished(
                    report_path,
                    step_report_context,
                    receipt,
                    worker_status=step_status,
                    memory_gb=round(mem_gb, 2),
                    raw_detail=str(raw_detail),
                )

            # 进度推送：步骤完成
            if _wf_id:
                publish_event(_wf_id, {
                    'event_type': 'step.done',
                    'segment': _seg_idx,
                    'step': i,
                    'step_total': len(skill_chain),
                    'skill_id': step_skill_id,
                    'status': step_status,
                    'progress': (i + 1) / len(skill_chain),
                    'message': f'{step_skill_id}: {step_status}',
                    'memory_gb': round(mem_gb, 2),
                })

            # 从 detail 中提取技能级状态
            _inner_status = receipt.get('status', '')
            _inner_outputs = receipt.get('outputs', {}) or {}
            detail_str = str(raw_detail)

            # 灌本步 outputs 到 _chain_outputs 供后续步模板引用
            _step_key = step.get('step_id') or step_skill_id
            if _step_key and _inner_outputs:
                _chain_outputs[_step_key] = _inner_outputs

            effective_status = _inner_status or step_status
            if effective_status in ('AUDIT_FAILED', 'NEEDS_ATTENTION'):
                _write_audit(
                    'CHAIN_AUDIT_FAILED',
                    f'step {i} ({step_skill_id}) 审计未通过: {effective_status}',
                    i,
                    step_skill_id,
                )
                _finalize_runtime_report('CHAIN_AUDIT_FAILED', error=f'step {i} ({step_skill_id}) 审计未通过: {effective_status}')
                return {
                    'task_id': task_id, 'status': 'CHAIN_AUDIT_FAILED',
                    'failed_step': i, 'failed_skill': step_skill_id,
                    'detail': detail_str,
                    'chain_results': chain_results,
                    'report_path': report_path,
                    'message': f'链在 step {i} ({step_skill_id}) 审计未通过，已按后台批处理策略中止',
                }

            if _inner_status == 'BLOCKED':
                _write_audit('CHAIN_BLOCKED', f'step {i} blocked: {step_skill_id}')
                _finalize_runtime_report('CHAIN_BLOCKED', error=f'step {i} blocked: {step_skill_id}')
                return {
                    'task_id': task_id, 'status': 'CHAIN_BLOCKED',
                    'failed_step': i, 'detail': detail_str,
                    'chain_results': chain_results,
                    'report_path': report_path,
                }

            if step_status != 'SUCCESS':
                _write_audit('CHAIN_ABORTED', f'step {i} failed: {step_skill_id}')
                _finalize_runtime_report('CHAIN_ABORTED', error=f'step {i} failed: {step_skill_id}')
                return {
                    'task_id': task_id, 'status': 'CHAIN_ABORTED',
                    'failed_step': i, 'chain_results': chain_results,
                    'report_path': report_path,
                }

        _write_audit('CHAIN_SUCCESS', f'{len(chain_results)} steps completed')

        # ── 运行时报告收尾 ──
        total_elapsed_min = (time.time() - _t_chain_start) / 60
        if not is_subchain:
            _finalize_runtime_report('SUCCESS')

        result = {
            'task_id': task_id, 'status': 'SUCCESS',
            'chain_results': chain_results,
            'report_path': report_path,
        }
        return result

    except TimeoutError:
        tb = traceback.format_exc()
        _write_audit('TIMEOUT', tb)
        _finalize_runtime_report('TIMEOUT', error='任务超时', tb=tb)
        raise

    except Exception as exc:
        from celery.exceptions import SoftTimeLimitExceeded
        if isinstance(exc, SoftTimeLimitExceeded):
            _write_audit('TIMEOUT', 'Soft time limit exceeded (9 minutes). Terminating worker.')
            _finalize_runtime_report('TIMEOUT', error='Soft time limit exceeded (9 minutes). Terminating worker.')
            if worker and hasattr(worker, 'shutdown'):
                try:
                    worker.shutdown()
                except Exception:
                    pass
            raise

        tb = traceback.format_exc()
        _write_audit('CHAIN_ERROR', tb)
        _finalize_runtime_report('CHAIN_ERROR', error=f'{type(exc).__name__}: {exc}', tb=tb)
        raise self.retry(exc=exc)

    finally:
        if worker and hasattr(worker, 'shutdown'):
            try:
                worker.shutdown()
            except Exception:
                logger.warning(f'[{task_id}] Worker shutdown 异常', exc_info=True)


# ═══════════════════════════════════════════════════════════════
# 单技能执行
# ═══════════════════════════════════════════════════════════════

@app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name='core.tasks.execute_dcc_skill'
)
def execute_dcc_skill(self, payload: dict):
    """
    单技能执行：创建新 DCC 进程 → 执行 → 质检 → 发布 → 退出。
    """
    from core.schemas import SkillPayloadSchema
    from pydantic import ValidationError

    try:
        validated_payload = SkillPayloadSchema(**payload)
        payload = validated_payload.model_dump()
    except ValidationError as e:
        task_id = payload.get('task_id', 'unknown_task')
        skill_id = payload.get('skill_id', 'unknown_skill')
        error_msg = f"输入数据 Schema 校验失败 (Fail-Fast):\n{str(e)}"
        
        from core.bootstrap import cfg
        _audit_file = Path(cfg.PROJECT_ROOT) / 'audit' / f'{task_id}.json'
        def _write_err(status, msg):
            try:
                import json
                _audit_file.parent.mkdir(parents=True, exist_ok=True)
                with open(_audit_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({'task_id': task_id, 'skill_id': skill_id, 'status': status, 'detail': msg, 'ts': time.time()}, ensure_ascii=False) + '\\n')
            except Exception: pass
        
        _write_err('ERROR', error_msg)
        raise ValueError(error_msg)

    task_id = payload.get('task_id', self.request.id)
    skill_id = payload.get('skill_id', '')
    project = payload.get('project', 'default')
    asset_name = payload.get('asset_name', 'untitled')
    audit_path = _get_audit_path(task_id)

    def _write_audit(status, detail=''):
        entry = {
            'task_id': task_id, 'skill_id': skill_id,
            'status': status, 'ts': time.time(), 'detail': detail,
            'attempt': self.request.retries + 1,
        }
        with open(audit_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')

    worker = None
    _t_start = time.time()
    # 沙盒目录提前到 try 之前，所有出口都能用
    from core.run_archive import create_run_dir
    run_dir = create_run_dir(task_id, project, asset_name, payload.get('submitted_at'))
    info_dir = run_dir / '.info'
    info_dir.mkdir(parents=True, exist_ok=True)
    report_path = _report_writer.get_report_path(run_dir)
    report_context = {
        'task_id': task_id,
        'asset_name': asset_name,
        'project': project,
        'source_path': payload.get('source_path', ''),
        'run_dir': str(run_dir),
    }
    _report_writer.init_report(report_path, report_context)
    staged_records = []

    def _record_file_staged(record: dict):
        staged_records.append(record)
        _write_audit('FILE_STAGED', json.dumps(record, ensure_ascii=False))
        _report_writer.upsert_file_staged(report_path, staged_records)

    def _finalize_runtime_report(status: str, error: str = '', tb: str = ''):
        elapsed_min = (time.time() - _t_start) / 60
        ctx = dict(report_context)
        ctx['source_path'] = payload.get('source_path', '') or ctx.get('source_path', '')
        _report_writer.finalize_report(
            report_path, ctx, status, elapsed_min=elapsed_min,
            error=error, traceback_text=tb,
        )
    def _translate_error(detail: str, skill_id: str) -> str:
        """对底层原生报错进行业务级脱水转译"""
        if "No such file or directory" in detail or "FileNotFoundError" in detail or "文件不存在或为空" in detail:
            return f"[文件读取异常] 尝试读取的源文件不存在或被占用，请检查上游环节是否已正确发布。原生报错: {detail[:200]}"
        if "IndexError" in detail and "list index out of range" in detail:
            return f"[索引越界] {skill_id} 技能执行时遇到数组越界，这通常是因为场景中缺失预期的节点或组件（如空组、空层级）。原生报错: {detail[:200]}"
        if "KeyError" in detail:
            return f"[数据缺失] 缺少关键数据键值。原生报错: {detail[:200]}"
        if "RuntimeError" in detail and "Object does not exist" in detail:
            return f"[节点丢失] Maya/Blender 场景中找不到指定的节点，可能是因为名称被篡改或已被删除。原生报错: {detail[:200]}"
        return detail

    try:
        _write_audit('STARTED')
        _reload_skill_registry()
        dcc_type = get_skill_dcc(skill_id)

        # 自动定位源文件
        source_path = payload.get('source_path', '')
        if not source_path and skill_id not in _SKIP_AUDIT_SKILLS:
            _t_resolve = time.time()
            try:
                resolver = _get_resolver(project)
                # 尝试按阶段优先级定位
                resolved = resolver.resolve_by_stage('chr', asset_name)
                if resolved and resolved.get('path'):
                    source_path = resolved['path']
                    logger.info(f'[{task_id}] AssetResolver 定位到: {source_path}')
                    _record_file_staged({
                        'node': '资产路径解析',
                        'param': 'asset_name',
                        'input': asset_name,
                        'output': source_path,
                        'status': 'SUCCESS',
                        'elapsed_sec': time.time() - _t_resolve,
                    })
                else:
                    _record_file_staged({
                        'node': '资产路径解析',
                        'param': 'asset_name',
                        'input': asset_name,
                        'output': '',
                        'status': '未找到',
                        'elapsed_sec': time.time() - _t_resolve,
                    })
            except Exception as e:
                _record_file_staged({
                    'node': '资产路径解析',
                    'param': 'asset_name',
                    'input': asset_name,
                    'output': '',
                    'error': str(e),
                    'elapsed_sec': time.time() - _t_resolve,
                })

        _update_progress(self, 'STARTING_WORKER', f'正在启动 {dcc_type.capitalize()} 后台进程')
        worker = _create_worker(dcc_type, source_path)

        # 自动打开源文件或清空场景（无条件沙盒化）
        if source_path and dcc_type != 'pipeline':
            import shutil as _shutil
            src_p = Path(source_path)
            try:
                src_p.resolve().relative_to(run_dir.resolve())
            except ValueError:
                try:
                    local_path = str(run_dir / src_p.name)
                    reused = os.path.exists(local_path)
                    _t_copy = time.time()
                    if not reused:
                        _shutil.copy2(source_path, local_path)
                    logger.info(f'[{task_id}] 沙盒化: {source_path} → {local_path}')
                    _record_file_staged({
                        'role': 'source_path',
                        'origin': source_path,
                        'sandbox': local_path,
                        'reused': reused,
                        'elapsed_sec': time.time() - _t_copy,
                    })
                    source_path = local_path
                except Exception as e:
                    logger.warning(f'[{task_id}] 沙盒化失败，使用原路径: {e}')
            payload['source_path'] = source_path
            _write_audit('CHAIN_OPEN_FILE', source_path)
            _report_writer.upsert_open_scene(report_path, source_path, 'RUNNING')
            _update_progress(self, 'OPENING_FILE', f'正在打开源文件: {source_path}')
            _t_open = time.time()
            ok, err = _open_source_file(worker, task_id, source_path, dcc_type)
            open_elapsed = time.time() - _t_open
            if not ok:
                meaningful_err = _translate_error(err, "open_file")
                _write_audit('OPEN_FILE_FAILED', meaningful_err)
                _report_writer.upsert_open_scene(
                    report_path, source_path, 'ERROR',
                    elapsed_sec=open_elapsed, error=meaningful_err,
                )
                _finalize_runtime_report('SKILL_ERROR', error=meaningful_err)
                return {'task_id': task_id, 'status': 'SKILL_ERROR',
                        'detail': f'打开文件失败: {source_path} — {meaningful_err}',
                        'report_path': report_path}
            _write_audit('CHAIN_FILE_OPENED', source_path)
            _report_writer.upsert_open_scene(report_path, source_path, 'SUCCESS', elapsed_sec=open_elapsed)
        elif not source_path and dcc_type in ('maya', 'blender'):
            _open_source_file(worker, task_id, "", dcc_type)

        # 执行技能
        payload['source_path'] = source_path
        payload['run_dir'] = str(run_dir)
        payload['info_dir'] = str(info_dir)
        payload.setdefault('extra_params', {})
        payload['extra_params'].setdefault('run_dir', str(run_dir))
        payload['extra_params'].setdefault('info_dir', str(info_dir))
        _update_progress(self, 'EXECUTING_SKILL', f'正在执行技能: {skill_id}', '1/1')
        _write_audit('STEP_START', f'step 0: {skill_id}')
        step_report_context = {
            'step_index': 0,
            'step_total': 1,
            'skill_id': skill_id,
            'parameters': payload.get('parameters', {}),
            'source_path': source_path,
        }
        _report_writer.upsert_step_started(report_path, step_report_context)
        result = worker.run_skill(payload)
        logger.info(f'[{task_id}] 技能执行完成: status={result["status"]}')

        raw_detail = result.get('detail', '')
        if not raw_detail and isinstance(result, dict) and (
            'summary' in result or 'outputs' in result or 'skill_id' in result
        ):
            raw_detail = json.dumps(result, ensure_ascii=False, default=str)

        if result['status'] != 'SUCCESS':
            raw_status = result.get('status', 'ERROR') or 'ERROR'
            translated_err = _translate_error(str(raw_detail), skill_id) if raw_status == 'ERROR' else raw_detail
            raw_detail = translated_err
            result['detail'] = translated_err
            receipt = _report_writer.extract_receipt(raw_detail or result, skill_id, raw_status)
            if receipt.get('status') == 'SUCCESS':
                receipt['status'] = raw_status
            _report_writer.upsert_step_finished(
                report_path, step_report_context, receipt,
                worker_status=raw_status, raw_detail=str(raw_detail),
            )
            audit_status = f'STEP_{raw_status}' if raw_status in (
                'BLOCKED', 'AUDIT_FAILED', 'NEEDS_ATTENTION'
            ) else 'STEP_ERROR'
            _write_audit(audit_status, translated_err)
            _finalize_runtime_report(raw_status if raw_status != 'ERROR' else 'SKILL_ERROR',
                                     error=str(translated_err))
            return {'task_id': task_id, 'status': raw_status if raw_status != 'ERROR' else 'SKILL_ERROR',
                    'detail': translated_err,
                    'report_path': report_path}

        receipt = _report_writer.extract_receipt(raw_detail or result, skill_id, 'SUCCESS')
        _report_writer.upsert_step_finished(
            report_path, step_report_context, receipt,
            worker_status='SUCCESS', raw_detail=str(raw_detail),
        )
        _write_audit('STEP_SUCCESS', str(raw_detail))
        _write_audit('CHAIN_SUCCESS', '1 step completed')

        _finalize_runtime_report('SUCCESS')

        ret = {'task_id': task_id, 'status': 'SUCCESS',
               'detail': result.get('detail', ''),
               'report_path': report_path}
        return ret

    except TimeoutError:
        tb = traceback.format_exc()
        _write_audit('TIMEOUT', tb)
        _finalize_runtime_report('TIMEOUT', error='任务超时', tb=tb)
        raise

    except Exception as exc:
        from celery.exceptions import SoftTimeLimitExceeded
        if isinstance(exc, SoftTimeLimitExceeded):
            _write_audit('TIMEOUT', 'Soft time limit exceeded (9 minutes). Terminating worker.')
            _finalize_runtime_report('TIMEOUT', error='Soft time limit exceeded (9 minutes). Terminating worker.')
            if worker and hasattr(worker, 'shutdown'):
                try:
                    worker.shutdown()
                except Exception:
                    pass
            raise

        tb = traceback.format_exc()
        _write_audit('RETRY', tb)
        _finalize_runtime_report('ERROR', error=f'{type(exc).__name__}: {exc}', tb=tb)
        raise self.retry(exc=exc)

    finally:
        if worker and hasattr(worker, 'shutdown'):
            try:
                worker.shutdown()
            except Exception:
                logger.warning(f'[{task_id}] Worker shutdown 异常', exc_info=True)


# ── 工作流执行（跨 DCC 分段编排） ──

@app.task(
    bind=True,
    name='core.tasks.execute_workflow',
    max_retries=0,
)
def execute_workflow(self, payload: dict):
    """
    工作流执行：支持跨 DCC 编排 + 断点恢复 + 实时进度推送。

    将步骤按 DCC 类型分段（Segment），每段通过 apply_async 异步派发，
    段间通过 Redis 持久化 outputs 字典传递中间结果。

    断点恢复：重新提交同一 task_id 时，自动跳过已完成段。
    进度推送：每个 segment/step 的开始/结束事件通过 Redis Pub/Sub 推送。
    产物归档：所有报告统一写入 runs/{wf_id}/ 目录。
    """
    task_id = payload.get('task_id', self.request.id)
    workflow_id = payload.get('workflow_id', '')
    source_path = payload.get('source_path', '')
    project = payload.get('project', 'default')
    asset_name = payload.get('asset_name', 'untitled')
    extra_params = payload.get('extra_params', {})
    resume_mode = payload.get('resume', False)

    audit_path = _get_audit_path(task_id)

    def _write_audit(status, detail=''):
        entry = {
            'task_id': task_id, 'workflow_id': workflow_id,
            'status': status, 'ts': time.time(), 'detail': detail,
        }
        with open(audit_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')

    try:
        _write_audit('WORKFLOW_STARTED', f'workflow={workflow_id}')
        publish_event(task_id, {
            'event_type': 'workflow.start',
            'workflow_id': workflow_id,
            'asset_name': asset_name,
            'message': f'工作流启动: {workflow_id}',
            'progress': 0.0,
        })

        # 加载工作流定义
        from core.workflow_engine import load_workflow
        from core.config_loader import load_project_config
        wf = load_workflow(workflow_id)
        steps = wf.get('steps', [])
        
        project_config = load_project_config(project)

        if not steps:
            _write_audit('WORKFLOW_ERROR', '工作流无步骤')
            return {'task_id': task_id, 'status': 'WORKFLOW_ERROR', 'error': '工作流无步骤'}

        # ── 创建运行目录(沙盒) + 统一报告 ──
        run_dir = create_run_dir(task_id, project, asset_name, payload.get('submitted_at'))
        master_report_path = _report_writer.get_report_path(run_dir)
        _t_workflow_start = time.time()
        report_context = {
            'task_id': task_id,
            'asset_name': asset_name,
            'project': project,
            'workflow_id': workflow_id,
            'source_path': source_path,
            'run_dir': str(run_dir),
        }
        _report_writer.init_report(master_report_path, report_context)
        staged_records = []

        def _record_file_staged(record: dict):
            staged_records.append(record)
            _write_audit('FILE_STAGED', json.dumps(record, ensure_ascii=False))
            _report_writer.upsert_file_staged(master_report_path, staged_records)

        def _finalize_runtime_report(status: str, error: str = '', tb: str = ''):
            elapsed_min = (time.time() - _t_workflow_start) / 60
            ctx = dict(report_context)
            ctx['source_path'] = source_path
            _report_writer.finalize_report(
                master_report_path, ctx, status,
                elapsed_min=elapsed_min, error=error, traceback_text=tb,
            )
        
        # 将原始 source_path 和相关文件隔离到沙盒中
        def _stage_to_sandbox(src: str, role: str = 'source_path') -> str:
            if not src: return src
            src_path = Path(src)
            # 已经在本次 run_dir 内的文件无需重复拷贝
            try:
                src_path.resolve().relative_to(run_dir.resolve())
                _record_file_staged({
                    'role': role, 'origin': str(src_path), 'sandbox': str(src_path),
                    'skipped': True, 'reason': 'already_in_sandbox', 'elapsed_sec': 0.0,
                })
                return src
            except ValueError:
                pass
            if not src_path.exists():
                err_msg = f"源文件不存在: {src}"
                logger.error(f"[{task_id}] {err_msg}")
                _record_file_staged({
                    'role': role, 'origin': str(src_path), 'error': err_msg,
                    'elapsed_sec': 0.0,
                })
                raise FileNotFoundError(err_msg)
            dst_path = run_dir / src_path.name
            reused = False
            _t_copy = time.time()
            if not dst_path.exists() or not resume_mode:
                import shutil
                shutil.copy2(str(src_path), str(dst_path))
                logger.info(f"[{task_id}] 文件隔离到沙盒: {src_path.name}")
            else:
                reused = True
            _record_file_staged({
                'role': role, 'origin': str(src_path), 'sandbox': str(dst_path),
                'reused': reused, 'elapsed_sec': time.time() - _t_copy,
            })
            return str(dst_path)

        source_path = _stage_to_sandbox(source_path, role='source_path')
        for k, v in extra_params.items():
            if isinstance(v, str) and (v.endswith('.ma') or v.endswith('.mb') or v.endswith('.blend') or v.endswith('.json') or v.endswith('.abc')):
                extra_params[k] = _stage_to_sandbox(v, role=f'input.{k}')

        info_dir = run_dir / '.info'
        info_dir.mkdir(parents=True, exist_ok=True)
        extra_params.setdefault('source_path', source_path)
        extra_params.setdefault('run_dir', str(run_dir))
        extra_params.setdefault('info_dir', str(info_dir))
        
        # 覆写 payload 里的路径，保证后续所有的链式调用都使用沙盒内的文件
        payload['source_path'] = source_path
        payload['extra_params'] = extra_params

        try:
            cleanup_old_runs(max_age_days=7)
        except Exception:
            pass

        # ── 按 DCC 类型分段 ──
        _reload_skill_registry()
        segments = []  # [(dcc_type, [steps])]
        current_dcc = None
        current_steps = []

        for step in steps:
            step_dcc = get_skill_dcc(step['skill_id'])
            if step_dcc != current_dcc:
                if current_steps:
                    segments.append((current_dcc, current_steps))
                current_dcc = step_dcc
                current_steps = [step]
            else:
                current_steps.append(step)
        if current_steps:
            segments.append((current_dcc, current_steps))

        _write_audit('WORKFLOW_SEGMENTED',
                      f'{len(segments)} segments: {[(s[0], len(s[1])) for s in segments]}')

        # ── 断点恢复：从 Redis 恢复已完成段和 outputs ──
        completed_segs = get_completed_segments(task_id) if resume_mode else set()
        all_outputs = restore_outputs(task_id) if resume_mode else {}
        all_results = []
        total_steps_executed = 0
        collected_reports = [master_report_path]
        collected_outputs = {}

        for seg_idx, (seg_dcc, seg_steps) in enumerate(segments):
            # 断点恢复：跳过已完成段
            if seg_idx in completed_segs:
                _write_audit('SEGMENT_SKIPPED', f'seg {seg_idx}: 已完成，跳过')
                publish_event(task_id, {
                    'event_type': 'segment.skipped',
                    'segment': seg_idx,
                    'segment_total': len(segments),
                    'message': f'段 {seg_idx} 已完成，跳过',
                })
                continue

            # 模板变量替换 + chain payload 构建
            # workflow 只做"跨段 + 环境"级别的模板解析（input / config / 上段 outputs），
            # 本段内 step-to-step 的 {{outputs.<sibling>.xxx}} 由 chain 引擎在每步执行前补解析。
            resolved_steps = []
            for step in seg_steps:
                resolved_params = _resolve_template_vars(
                    step.get('parameters', {}), all_outputs, extra_params, project_config
                )
                resolved_step = {
                    'step_id': step.get('step_id', ''),
                    'skill_id': step['skill_id'],
                    'parameters': resolved_params,
                }
                if step.get('source_path'):
                    resolved_step['source_path'] = _resolve_template_vars(
                        {'source_path': step.get('source_path')},
                        all_outputs, extra_params, project_config
                    ).get('source_path')
                resolved_steps.append(resolved_step)

            seg_task_id = f'{task_id}_seg{seg_idx}'
            _write_audit('SEGMENT_START',
                          f'seg {seg_idx}: {seg_dcc}, {len(resolved_steps)} steps')
            _report_writer.upsert_segment(
                master_report_path,
                {
                    'segment_index': seg_idx,
                    'dcc': seg_dcc,
                    'step_count': len(resolved_steps),
                },
                'RUNNING',
            )
            publish_event(task_id, {
                'event_type': 'segment.start',
                'segment': seg_idx,
                'segment_total': len(segments),
                'dcc': seg_dcc,
                'steps': len(resolved_steps),
                'progress': seg_idx / len(segments),
                'message': f'段 {seg_idx}/{len(segments)}: {seg_dcc} ({len(resolved_steps)} 步)',
            })

            seg_source_path = source_path
            if resolved_steps and resolved_steps[0].get('source_path'):
                seg_source_path = resolved_steps[0]['source_path']
            elif 'resolve_asset' in all_outputs and 'source_path' in all_outputs['resolve_asset']:
                seg_source_path = all_outputs['resolve_asset']['source_path']

            # 构建链式 payload：steps 用 resolved（已解析 input/config/跨段 outputs），
            # chain 引擎内部还会按每步完成后的 _chain_outputs 补解析剩余模板。
            from core.manifest import get_dcc_queue
            queue = get_dcc_queue(seg_dcc)

            chain_payload = {
                'task_id': seg_task_id,
                'source_path': seg_source_path,
                'project': project,
                'asset_name': asset_name,
                'skill_chain': resolved_steps,       # 跨段已解析；同段模板保留待 chain 补解析
                'workflow_id': task_id,              # 注入 wf_id 供子链发布进度事件
                'segment_index': seg_idx,            # 注入段索引
                'run_dir': str(run_dir),             # 共用 workflow 沙盒，不要再造
                'report_path': master_report_path,   # 子链写同一个 REPORT.md
                '_chain_outputs_in': dict(all_outputs),  # 跨段 outputs，chain 补解析时合并
                'extra_params': dict(extra_params),      # 支持 {{input.xxx}}
                '_chain_config': project_config,         # 支持 {{config.x.y.z}}
            }

            # ── 异步派发子段，独立 Celery task context ──
            seg_async = execute_skill_chain.apply_async(
                args=[chain_payload], queue=queue, task_id=seg_task_id
            )

            # 轮询等待子段完成（Celery 禁止在 task 内 .get()）。
            # 不设置硬超时：大型 DCC 资产允许长时间运行，只在 worker 死亡时中断。
            _poll_start = time.time()
            seg_result = None
            from core.service_manager import is_worker_alive
            while True:
                time.sleep(3)
                if seg_async.ready():
                    raw = seg_async.result
                    if isinstance(raw, Exception):
                        seg_result = {'status': 'CHAIN_ERROR', 'error': str(raw), 'chain_results': []}
                    elif isinstance(raw, dict):
                        seg_result = raw
                    else:
                        seg_result = {'status': 'CHAIN_ERROR', 'error': f'意外结果类型: {type(raw)}', 'chain_results': []}
                    break
                
                # 防假死机制：如果负责执行的 Worker 进程已经死亡，直接阻断，避免死等 25 分钟
                worker_alive, _ = is_worker_alive(seg_dcc)
                if not worker_alive:
                    seg_result = {'status': 'CHAIN_ERROR', 'error': f'DCC Worker ({seg_dcc}) 进程意外死亡或丢失', 'chain_results': []}
                    break

            seg_status = seg_result.get('status', 'UNKNOWN')
            seg_chain_results = seg_result.get('chain_results', [])
            total_steps_executed += len(seg_chain_results)

            # 计算段耗时：首尾 chain_result 时间戳差（兜底 0）
            _seg_ts_min = 0.0
            if seg_chain_results:
                try:
                    _seg_start = seg_async.date_start if hasattr(seg_async, 'date_start') else None
                except Exception:
                    _seg_start = None
                _seg_ts_min = (time.time() - _poll_start) / 60

            # SEGMENT audit 填结构化 JSON，供 write_task_report workflow 模式消费
            seg_detail = {
                'dcc': seg_dcc,
                'step_count': len(seg_chain_results),
                'elapsed_min': round(_seg_ts_min, 2),
                'summary': {
                    'action': f'{seg_dcc} · {len(seg_chain_results)} 步',
                },
                'outputs': {
                    'report_path': seg_result.get('report_path', ''),
                },
            }
            if seg_result.get('error'):
                seg_detail['error'] = str(seg_result.get('error'))[:500]
            _write_audit(f'SEGMENT_{seg_status}',
                          json.dumps(seg_detail, default=str, ensure_ascii=False))
            _report_writer.upsert_segment(
                master_report_path,
                {
                    'segment_index': seg_idx,
                    'dcc': seg_dcc,
                    'step_count': len(seg_chain_results),
                },
                seg_status,
                elapsed_min=round(_seg_ts_min, 2),
                error=seg_detail.get('error', ''),
            )

            publish_event(task_id, {
                'event_type': 'segment.done',
                'segment': seg_idx,
                'segment_total': len(segments),
                'status': seg_status,
                'progress': (seg_idx + 1) / len(segments),
                'message': f'段 {seg_idx} 完成: {seg_status}',
            })

            all_results.append({
                'segment': seg_idx,
                'dcc': seg_dcc,
                'status': seg_status,
                'chain_results': seg_result.get('chain_results', []),
            })

            # 提取本段 outputs 供后续段使用
            for i, cr in enumerate(seg_result.get('chain_results', [])):
                try:
                    inner = json.loads(cr.get('detail', '{}'))
                    step = resolved_steps[i]
                    _used_id = step.get('step_id') or inner.get('step_id', '') or cr.get('skill_id', '')
                    outputs = inner.get('outputs', {})
                    if _used_id and outputs:
                        all_outputs[_used_id] = outputs
                except Exception:
                    pass

            # 段失败则中断工作流
            if seg_status not in ('SUCCESS', 'CHAIN_SUCCESS'):
                workflow_fail_status = (
                    'WORKFLOW_AUDIT_FAILED'
                    if seg_status in ('AUDIT_FAILED', 'STEP_AUDIT_FAILED', 'CHAIN_AUDIT_FAILED')
                    else 'WORKFLOW_ABORTED'
                )
                _write_audit(workflow_fail_status, f'segment {seg_idx} failed: {seg_status}')
                _finalize_runtime_report(workflow_fail_status, error=f'segment {seg_idx} failed: {seg_status}')
                publish_event(task_id, {
                    'event_type': 'workflow.error',
                    'failed_segment': seg_idx,
                    'status': workflow_fail_status,
                    'progress': (seg_idx + 1) / len(segments),
                    'message': f'工作流在段 {seg_idx} 中止: {seg_status}',
                })
                return {
                    'task_id': task_id,
                    'workflow_id': workflow_id,
                    'status': workflow_fail_status,
                    'failed_segment': seg_idx,
                    'segments': all_results,
                    'report_path': master_report_path,
                }

            # 只有成功段才持久化 outputs + 标记段完成，避免恢复时跳过失败段。
            persist_outputs(task_id, all_outputs)
            mark_segment_done(task_id, seg_idx)

        # ── 工作流成功完成 ──
        total_elapsed_min = (time.time() - _t_workflow_start) / 60
        _write_audit('WORKFLOW_SUCCESS', f'{len(segments)} segments completed')
        _finalize_runtime_report('SUCCESS')

    # (审计日志已直接写入沙盒，无需手动复制)

        # 收集所有产出路径并写入 manifest。机器中间产物保留在 outputs，
        # 只有给人阅读的报告才进入 reports 并归档到沙盒根目录。
        for step_id, step_outputs in all_outputs.items():
            for field, value in step_outputs.items():
                if isinstance(value, str) and value.lower().endswith(('.json', '.md', '.ma', '.mb', '.abc', '.blend', '.html', '.htm')):
                    collected_outputs[f'{step_id}.{field}'] = value
                    if is_report_path(value):
                        copied_report = copy_to_run_dir(task_id, value)
                        collected_reports.append(copied_report or value)

        for removed_path in cleanup_root_machine_duplicates(run_dir):
            _write_audit('ROOT_DUPLICATE_CLEANED', removed_path)

        write_manifest(
            wf_id=task_id,
            workflow_id=workflow_id,
            asset_name=asset_name,
            project=project,
            status='SUCCESS',
            elapsed_min=total_elapsed_min,
            inputs={'source_path': source_path, **extra_params},
            outputs=collected_outputs,
            reports=collected_reports,
            segments=[{'seg': r['segment'], 'dcc': r['dcc'], 'status': r['status']} for r in all_results],
        )

        # 清理 Redis 临时状态
        cleanup_workflow_state(task_id)

        publish_event(task_id, {
            'event_type': 'workflow.done',
            'status': 'SUCCESS',
            'progress': 1.0,
            'elapsed_min': round(total_elapsed_min, 2),
            'message': f'工作流完成，共 {len(segments)} 段 {total_steps_executed} 步',
            'report_path': master_report_path,
        })

        return {
            'task_id': task_id,
            'workflow_id': workflow_id,
            'status': 'SUCCESS',
            'segments': all_results,
            'report_path': master_report_path,
        }

    except Exception as exc:
        tb = traceback.format_exc()
        _write_audit('WORKFLOW_ERROR', tb)
        publish_event(task_id, {
            'event_type': 'workflow.error',
            'status': 'WORKFLOW_ERROR',
            'error': str(exc),
            'message': f'工作流异常: {type(exc).__name__}',
        })
        if 'master_report_path' in locals() and 'run_dir' in locals():
            try:
                _finalize_runtime_report('WORKFLOW_ERROR', error=f'{type(exc).__name__}: {exc}', tb=tb)
            except Exception:
                pass
        return {
            'task_id': task_id,
            'status': 'WORKFLOW_ERROR',
            'error': f'{type(exc).__name__}: {exc}',
        }


def _resolve_template_vars(params: dict, outputs: dict, extra_params: dict, config: dict = None) -> dict:
    """替换参数中的模板变量。

    支持格式：
      {{outputs.step_id.field}}                   → 从前序步骤输出中取值
      {{input.xxx}}                               → 从工作流 extra_params 中取值
      {{config.x.y.z}}                            → 从全局配置中取值（支持 .0 索引列表）
      {{ <expr> | replace('A', 'B') }}            → 对表达式的取值做一次字符串替换
      {{ <expr> | replace('A', 'B') | replace('C', 'D') }} → 可串联多个 replace

    行为：整体匹配单个 {{ ... }} 占位符时，直接替换为解析值（保留 non-str 类型，
    如 int/list）；若整串仅为占位符以外还有其他字面量，则走字符串拼接（强转 str）。
    """
    import re

    config = config or {}
    placeholder_re = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')
    # expr 内部语法：
    #   base = outputs.<step>.<field> | input.<name> | config.<dot.path>
    #   tail = ( \s*\|\s* replace\(\s*'X'\s*,\s*'Y'\s*\) )*
    base_re = re.compile(
        r'^(?P<kind>outputs|input|config)\.(?P<path>[\w\.]+?)'
        r'(?P<tail>(?:\s*\|\s*[^|]+)*)$'
    )
    filter_re = re.compile(
        r'\|\s*replace\(\s*'
        r'(?P<a>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')\s*,\s*'
        r'(?P<b>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')\s*\)'
    )

    def _unquote(s: str) -> str:
        s = s.strip()
        if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
            return bytes(s[1:-1], 'utf-8').decode('unicode_escape')
        return s

    def _resolve_base(kind: str, path: str):
        keys = path.split('.')
        if kind == 'outputs':
            # outputs.<step>.<field>，支持 outputs.step.result.path 这样的嵌套字段。
            if len(keys) < 2:
                return None, False
            step_id = keys[0]
            step_outputs = outputs.get(step_id, {})
            field = '.'.join(keys[1:])
            if isinstance(step_outputs, dict) and field in step_outputs:
                return step_outputs[field], True
            curr = step_outputs
            for key in keys[1:]:
                if isinstance(curr, dict) and key in curr:
                    curr = curr[key]
                elif isinstance(curr, list) and key.isdigit() and int(key) < len(curr):
                    curr = curr[int(key)]
                else:
                    return None, False
            return curr, True
        if kind == 'input':
            if keys[0] in extra_params:
                curr = extra_params[keys[0]]
                for key in keys[1:]:
                    if isinstance(curr, dict) and key in curr:
                        curr = curr[key]
                    elif isinstance(curr, list) and key.isdigit() and int(key) < len(curr):
                        curr = curr[int(key)]
                    else:
                        return None, False
                return curr, True
            return None, False
        if kind == 'config':
            curr = config
            for key in keys:
                if isinstance(curr, dict) and key in curr:
                    curr = curr[key]
                elif isinstance(curr, list) and key.isdigit() and int(key) < len(curr):
                    curr = curr[int(key)]
                else:
                    return None, False
            return curr, True
        return None, False

    def _path_basename(value) -> str:
        norm = str(value).replace('\\', '/').rstrip('/')
        return norm.rsplit('/', 1)[-1]

    def _path_dirname(value) -> str:
        norm = str(value).replace('\\', '/').rstrip('/')
        return norm.rsplit('/', 1)[0] if '/' in norm else ''

    def _apply_filter(value, token: str):
        token = token.strip()
        if not token:
            return value
        replace_match = filter_re.fullmatch('|' + token)
        if replace_match:
            a = _unquote(replace_match.group('a'))
            b = _unquote(replace_match.group('b'))
            return str(value).replace(a, b)
        if token == 'basename':
            return _path_basename(value)
        if token == 'stem':
            return os.path.splitext(_path_basename(value))[0]
        if token == 'dirname':
            return _path_dirname(value)
        raise ValueError(f'不支持的模板过滤器: {token}')

    def _eval_expr(expr: str):
        """解析一个 {{ ... }} 内部的表达式，返回 (value, ok)。"""
        m = base_re.match(expr.strip())
        if not m:
            return None, False
        val, ok = _resolve_base(m.group('kind'), m.group('path'))
        if not ok:
            return None, False
        tail = m.group('tail') or ''
        for token in tail.split('|')[1:]:
            try:
                val = _apply_filter(val, token)
            except ValueError:
                return None, False
        return val, True

    def _resolve_string(s: str):
        # 整串就是单个占位符：保留原值类型
        full = placeholder_re.fullmatch(s.strip())
        if full:
            val, ok = _eval_expr(full.group(1))
            return val if ok else s

        # 内嵌多个占位符或混合字面量：按字符串拼接
        def _sub(m):
            val, ok = _eval_expr(m.group(1))
            return str(val) if ok else m.group(0)
        return placeholder_re.sub(_sub, s)

    resolved = {}
    for k, v in params.items():
        resolved[k] = _resolve_string(v) if isinstance(v, str) else v
    return resolved
