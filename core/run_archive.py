# core/run_archive.py
# ── 工作流产物归档模块 ──
#
# 每个工作流运行在 runs/{wf_id}/ 下创建独立目录，
# 运行结束后生成 manifest.json 统一记录所有输入/输出/报告路径。
# 支持按 TTL 自动清理过期运行目录。

import json
import time
import shutil
import logging
from pathlib import Path

from core.bootstrap import cfg as _cfg

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)
RUNS_DIR = PROJECT_ROOT / 'runs'
PROJECTS_DIR = PROJECT_ROOT / 'projects'

def create_run_dir(wf_id: str, project: str = None, asset_name: str = None, submitted_at: float = None) -> Path:
    """
    创建任务沙盒(Task Sandbox)目录。
    为了保证稳定，如果多次调用同一 wf_id，应返回相同的目录。
    格式: projects/{project}/tasks/{datetime}_{asset_name}_{wf_id}
    """
    import datetime

    # 尝试从 Redis 读取已存在的映射，保证多进程一致性
    from core.service_manager import is_redis_alive
    try:
        import redis
        if is_redis_alive():
            r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
            cached_dir = r.hget(f'wf:{wf_id}:state', 'sandbox_dir')
            if cached_dir:
                run_dir = Path(cached_dir)
                run_dir.mkdir(parents=True, exist_ok=True)
                return run_dir
    except Exception:
        pass

    if project and asset_name:
        dt = datetime.datetime.fromtimestamp(submitted_at) if submitted_at else datetime.datetime.now()
        date_str = dt.strftime('%Y%m%d_%H%M%S')
        run_dir = PROJECT_ROOT / 'projects' / project / f'{date_str}_{asset_name}_{wf_id}'
    else:
        run_dir = PROJECT_ROOT / 'runs' / wf_id

    run_dir.mkdir(parents=True, exist_ok=True)

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
    try:
        from core.service_manager import is_redis_alive
        import redis
        if is_redis_alive():
            r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
            cached_dir = r.hget(f'wf:{wf_id}:state', 'sandbox_dir')
            if cached_dir:
                return Path(cached_dir)
    except Exception:
        pass
    return PROJECT_ROOT / 'runs' / wf_id


def get_run_report_path(wf_id: str, asset_name: str, project: str = None) -> str:
    """获取工作流统一报告路径"""
    run_dir = create_run_dir(wf_id, project, asset_name)
    return str(run_dir / f'{asset_name}_{wf_id}.md')


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

    run_dir = create_run_dir(wf_id, project, asset_name)
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
        'reports': reports,
        'segments': segments or [],
    }

    manifest_path = run_dir / 'manifest.json'
    manifest_path.write_text(
        json.dumps(manifest, indent=2, default=str, ensure_ascii=False),
        encoding='utf-8',
    )
    logger.info(f'[{wf_id}] Manifest 已写入: {manifest_path}')
    return str(manifest_path)


def copy_to_run_dir(wf_id: str, src_path: str, filename: str = None) -> str:
    """
    将文件拷贝到运行目录内（可选重命名）。

    用于将散落在各处的审计日志、报告等集中归档。
    返回目标路径。
    """
    src = Path(src_path)
    if not src.exists():
        return ''

    run_dir = get_run_dir(wf_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    dst = run_dir / (filename or src.name)
    try:
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

    if RUNS_DIR.exists():
        for run_dir in RUNS_DIR.iterdir():
            if not run_dir.is_dir():
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
