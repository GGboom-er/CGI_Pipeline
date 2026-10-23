# cgi_pipeline/core/run_archive.py
# ── 工作流产物归档模块 ──
#
# 每个工作流运行在 runs/{wf_id}/ 下创建独立目录，
# 运行结束后生成 manifest.json 统一记录所有输入/输出/报告路径。
# 支持按 TTL 自动清理过期运行目录。

import json
import time
import shutil
import logging
import re
import filecmp
from contextlib import contextmanager
from pathlib import Path

from cgi_pipeline.core.bootstrap import cfg as _cfg

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)
RUNS_DIR = PROJECT_ROOT / 'runs'
PROJECTS_DIR = PROJECT_ROOT / 'projects'
REPORT_SUFFIXES = {'.md', '.html', '.htm'}
MACHINE_OUTPUT_SUFFIXES = {'.json', '.abc'}


def _safe_segment(value: str, fallback: str = 'untitled') -> str:
    text = str(value or fallback).strip() or fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
    text = re.sub(r'\s+', '_', text)
    return text[:80] or fallback


def _find_existing_run_dir(wf_id: str, project: str = None,
                           business_task_id: str = '') -> Path | None:
    """Redis 不可用时，通过 .info/run_state.json 找回同一 task 的沙盒。"""
    if not wf_id or not project:
        return None
    project_dir = PROJECT_ROOT / 'projects' / _safe_segment(project, 'default')
    if not project_dir.exists():
        return None
    for state_path in project_dir.glob('*/.info/run_state.json'):
        try:
            data = json.loads(state_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        if isinstance(data, dict) and data.get('wf_id') == wf_id:
            from cgi_pipeline.core.workspace import _validate_run_identity
            return _validate_run_identity(state_path.parent.parent, wf_id, project, business_task_id)[0]
    return None


def _write_run_state(run_dir: Path, wf_id: str, project: str = None, asset_name: str = None,
                     business_task_id: str = None) -> None:
    # Imported at call time: workspace already owns validation, locking and
    # atomic state writes, and itself imports this allocation module.
    from cgi_pipeline.core.workspace import (
        _lock, _validate_run_identity, _validate_workspace_location, _write,
    )
    run_dir = _validate_workspace_location(run_dir, project or '')
    info_dir = run_dir / '.info'
    info_dir.mkdir(parents=True, exist_ok=True)
    with _lock(run_dir):
        state_path = info_dir / 'run_state.json'
        if state_path.exists() or state_path.is_symlink():
            _validate_run_identity(run_dir, wf_id, project, business_task_id or '')
            return
        state = {'wf_id': wf_id, 'project': project or '',
                 'asset_name': asset_name or '',
                 'created_at': time.strftime('%Y-%m-%d %H:%M:%S')}
        if business_task_id:
            state['business_task_id'] = business_task_id
        # Failure must reach the caller; a directory alone is not allocation.
        _write(state_path, state)

@contextmanager
def allocation_lock():
    """Serialize ordinary allocation and explicit legacy-workspace adoption."""
    from filelock import FileLock
    from cgi_pipeline.core.workspace import WorkspaceError
    allocation_root = PROJECT_ROOT / 'projects'
    if allocation_root.resolve() != PROJECT_ROOT.resolve() / 'projects':
        raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Managed project root cannot redirect outside its root.')
    if (allocation_root / '.allocation.lock').is_symlink():
        raise WorkspaceError('WORKSPACE_PATH_INVALID', 'Allocation lock cannot be a file link.')
    allocation_root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(allocation_root / '.allocation.lock'), timeout=30):
        yield


def create_run_dir(wf_id: str, project: str = None, asset_name: str = None,
                   submitted_at: float = None, *, directory_name: str = None,
                   business_task_id: str = None) -> Path:
    from cgi_pipeline.core.workspace import WorkspaceError, _validate_id
    _validate_id(wf_id)
    if business_task_id:
        _validate_id(business_task_id)
    if project is not None and (not project or any(char in project for char in '/\\:') or project in ('.', '..')):
        raise WorkspaceError('WORKSPACE_PROJECT_INVALID', 'Invalid project key.')
    with allocation_lock():
        return _create_run_dir_locked(
            wf_id, project, asset_name, submitted_at,
            directory_name=directory_name, business_task_id=business_task_id,
        )


def _create_run_dir_locked(wf_id: str, project: str = None, asset_name: str = None,
                           submitted_at: float = None, *, directory_name: str = None,
                           business_task_id: str = None) -> Path:
    """
    创建任务沙盒(Task Sandbox)目录。
    为了保证稳定，如果多次调用同一 wf_id，应返回相同的目录。
    格式: projects/{project}/{datetime}_{asset_name}
    task_id/wf_id 只写入 .info/run_state.json 和 manifest，不进入用户主要识别目录名。
    """
    import datetime

    from cgi_pipeline.core.workspace import _validate_run_identity, _validate_workspace_location
    # Redis is only a location hint. Validate identity before touching its target;
    # cache/identity failures must not disappear into the Redis availability path.
    from cgi_pipeline.core.service_manager import is_redis_alive
    cached_dir = None
    try:
        import redis
        if is_redis_alive():
            r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
            cached_dir = r.hget(f'wf:{wf_id}:state', 'sandbox_dir')
    except Exception:
        pass
    if cached_dir:
        return _validate_run_identity(cached_dir, wf_id, project, business_task_id or '')[0]

    existing = _find_existing_run_dir(wf_id, project, business_task_id or '')
    if existing:
        return existing

    if directory_name:
        safe_project = _safe_segment(project, 'default')
        safe_directory = _safe_segment(directory_name)
        run_dir = PROJECT_ROOT / 'projects' / safe_project / safe_directory
        if run_dir.exists():
            return _validate_run_identity(run_dir, wf_id, project, business_task_id or '')[0]
    elif project and asset_name:
        dt = datetime.datetime.fromtimestamp(submitted_at) if submitted_at else datetime.datetime.now()
        date_str = dt.strftime('%Y%m%d_%H%M%S')
        safe_project = _safe_segment(project, 'default')
        safe_asset = _safe_segment(asset_name, 'untitled')
        base = PROJECT_ROOT / 'projects' / safe_project / f'{date_str}_{safe_asset}'
        run_dir = base
        suffix = 1
        while run_dir.exists():
            state_path = run_dir / '.info' / 'run_state.json'
            try:
                data = json.loads(state_path.read_text(encoding='utf-8'))
            except Exception:
                data = {}
            if isinstance(data, dict) and data.get('wf_id') == wf_id:
                return _validate_run_identity(run_dir, wf_id, project, business_task_id or '')[0]
            run_dir = base.with_name(f'{base.name}_{suffix:02d}')
            suffix += 1
    else:
        run_dir = PROJECT_ROOT / 'runs' / wf_id

    run_dir = _validate_workspace_location(run_dir, project or '')
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_run_state(run_dir, wf_id, project, asset_name, business_task_id)

    # 保存回 Redis 供后续节点获取
    try:
        import redis
        if is_redis_alive():
            r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
            r.hset(f'wf:{wf_id}:state', 'sandbox_dir', str(run_dir))
    except Exception:
        pass

    return run_dir

def get_run_dir(wf_id: str) -> Path:
    """获取沙盒目录。优先从 Redis 读取。"""
    from cgi_pipeline.core.workspace import _validate_id, _validate_run_identity
    _validate_id(wf_id)
    cached_dir = None
    try:
        from cgi_pipeline.core.service_manager import is_redis_alive
        import redis
        if is_redis_alive():
            r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
            cached_dir = r.hget(f'wf:{wf_id}:state', 'sandbox_dir')
    except Exception:
        pass
    if cached_dir:
        from cgi_pipeline.core.workspace_relocation import relocation_for_root
        relocation = relocation_for_root(cached_dir)
        if relocation:
            cached_dir = relocation['target_root']
        return _validate_run_identity(cached_dir, wf_id, None)[0]
    return PROJECT_ROOT / 'runs' / wf_id


def get_run_report_path(wf_id: str, asset_name: str, project: str = None) -> str:
    """获取工作流统一报告路径"""
    run_dir = create_run_dir(wf_id, project, asset_name)
    return str(run_dir / 'REPORT.md')


def is_report_path(value) -> bool:
    """判断路径是否是给人阅读的报告，而不是机器中间产物。"""
    if not isinstance(value, (str, Path)):
        return False
    return Path(str(value)).suffix.lower() in REPORT_SUFFIXES


def _dedupe_report_paths(reports: list | None) -> list:
    """Manifest 只登记人读报告；JSON/ABC 等机器产物由 outputs 字段负责。"""
    result = []
    seen = set()
    for item in reports or []:
        if not is_report_path(item):
            continue
        path_text = str(item)
        if path_text in seen:
            continue
        result.append(path_text)
        seen.add(path_text)
    return result


def cleanup_root_machine_duplicates(run_dir: str | Path) -> list[str]:
    """
    清理沙盒根目录里与 .info 同名且内容完全一致的机器产物副本。

    只删除可证明已有 .info 权威副本的文件，避免误删人工放置或唯一产物。
    """
    run_dir = Path(run_dir)
    info_dir = run_dir / '.info'
    if not run_dir.exists() or not info_dir.exists():
        return []

    removed = []
    for path in run_dir.iterdir():
        if not path.is_file():
            continue
        if path.name == 'manifest.json':
            continue
        if path.suffix.lower() not in MACHINE_OUTPUT_SUFFIXES:
            continue

        info_path = info_dir / path.name
        if not info_path.exists() or not info_path.is_file():
            continue

        try:
            if path.stat().st_size != info_path.stat().st_size:
                continue
            if not filecmp.cmp(str(path), str(info_path), shallow=False):
                continue
            path.unlink()
            removed.append(str(path))
        except OSError as exc:
            logger.warning(f'根目录机器产物副本清理失败 {path}: {exc}')

    return removed


def write_manifest(
    wf_id: str,
    workflow_id: str,
    asset_name: str,
    project: str,
    status: str,
    elapsed_min: float,
    inputs: dict,
    outputs: dict,
    reports: list,
    segments: list = None,
    run_dir: str | Path | None = None,
) -> str:
    """
    生成 manifest.json，统一记录本次运行的所有输入/输出/报告路径。

    参数:
        wf_id: 运行 ID（如 wf-0ff021d8）
        workflow_id: 工作流定义 ID（如 tex_to_rig_verify_and_sync）
        asset_name: 资产名
        project: 项目代号
        status: 最终状态
        elapsed_min: 总耗时（分钟）
        inputs: 输入文件路径字典
        outputs: 输出文件路径字典
        reports: 报告文件路径列表
        segments: 段执行摘要列表

    返回:
        manifest 文件路径
    """
    import datetime

    run_dir = Path(run_dir) if run_dir is not None else create_run_dir(wf_id, project, asset_name)
    manifest = {
        'wf_id': wf_id,
        'workflow_id': workflow_id,
        'asset_name': asset_name,
        'project': project,
        'status': status,
        'elapsed_min': round(elapsed_min, 2),
        'created_at': datetime.datetime.now().isoformat(),
        'inputs': inputs,
        'outputs': outputs,
        'reports': _dedupe_report_paths(reports),
        'segments': segments or [],
    }

    from cgi_pipeline.core.workspace import operation_paths, _write
    manifest_path = (operation_paths(run_dir.parent, wf_id)['manifest']
                     if run_dir.name == '.info' else run_dir / 'manifest.json')
    # Keep the manifest's legacy default=str input contract while reusing the
    # workspace's guarded atomic JSON publisher.
    _write(manifest_path, json.loads(json.dumps(manifest, default=str)))
    logger.info(f'[{wf_id}] Manifest 已写入: {manifest_path}')
    return str(manifest_path)


def copy_to_run_dir(wf_id: str, src_path: str, filename: str = None, *, run_dir: str | Path | None = None) -> str:
    """
    将文件拷贝到运行目录内（可选重命名）。

    用于将散落在各处的审计日志、报告等集中归档。
    返回目标路径。
    """
    src = Path(src_path)
    if not src.exists():
        return ''

    run_dir = Path(run_dir) if run_dir else get_run_dir(wf_id)
    name = filename or src.name
    if (not isinstance(name, str) or name in ('', '.', '..')
            or Path(name).name != name or any(char in name for char in '/\\:')):
        raise ValueError('Archive filename must be a single local filename.')
    workspace_root = (run_dir.parent if run_dir.name == '.info' else
                      run_dir.parent.parent.parent if
                      run_dir.parent.name == 'operations' and run_dir.parent.parent.name == '.info'
                      else None)
    if workspace_root is not None:
        from cgi_pipeline.core.workspace import operation_paths
        paths = operation_paths(workspace_root, wf_id)
        if run_dir != paths['operation_dir']:
            raise ValueError('Archive directory does not match its operation record.')
        if run_dir.name == '.info' and not name.startswith(wf_id + '_'):
            name = f'{wf_id}_{name}'
        if any(run_dir/name == path for key,path in paths.items() if key != 'operation_dir'):
            raise ValueError('Archive filename is reserved for an operation record.')
    run_dir.mkdir(parents=True, exist_ok=True)
    dst = run_dir / name
    if dst.is_symlink():
        raise ValueError('Archive output cannot be a file link.')
    try:
        if src.resolve() == dst.resolve():
            return str(src)
        shutil.copy2(str(src), str(dst))
        return str(dst)
    except Exception as e:
        logger.warning(f'[{wf_id}] 文件归档失败 {src} → {dst}: {e}')
        return ''


def cleanup_old_runs(max_age_days: int = 7):
    """
    清理超龄运行目录。
    同时清理 runs/{wf_id}/ 和 projects/{project}/{YYYYMMDD_HHMMSS}_{asset}_{wf_id}/ 两种沙盒形态。
    """
    cutoff = time.time() - (max_age_days * 86400)
    cleaned = 0

    def managed_workspace(path):
        state_path = path / '.info' / 'run_state.json'
        if not state_path.exists():
            return False
        try:
            return bool(json.loads(state_path.read_text(encoding='utf-8')).get('workspace_mode'))
        except (OSError, ValueError, AttributeError):
            # An unreadable ownership record is not permission to discard work.
            return True

    if RUNS_DIR.exists():
        for run_dir in RUNS_DIR.iterdir():
            if not run_dir.is_dir():
                continue
            if managed_workspace(run_dir):
                continue
            try:
                if run_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(str(run_dir), ignore_errors=True)
                    cleaned += 1
            except Exception:
                pass

    if PROJECTS_DIR.exists():
        for proj_dir in PROJECTS_DIR.iterdir():
            if not proj_dir.is_dir():
                continue
            for sandbox in proj_dir.iterdir():
                if not sandbox.is_dir():
                    continue
                if managed_workspace(sandbox):
                    continue
                # 沙盒名模式：YYYYMMDD_HHMMSS_*，_开头的验证/实验目录不碰
                name = sandbox.name
                if not (len(name) >= 16 and name[:8].isdigit() and name[8] == '_'):
                    continue
                try:
                    if sandbox.stat().st_mtime < cutoff:
                        shutil.rmtree(str(sandbox), ignore_errors=True)
                        cleaned += 1
                except Exception:
                    pass

    if cleaned > 0:
        logger.info(f'已清理 {cleaned} 个超龄沙盒（>{max_age_days} 天）')
