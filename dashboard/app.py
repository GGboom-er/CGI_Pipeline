# dashboard/app.py
# ── CGI Pipeline v2.0 — Web Dashboard ──
#
# 实时展示任务执行状态、审计账本和技能注册表。
# 使用 FastAPI + SSE（Server-Sent Events）推送实时更新。
#
# 服务自举：Dashboard 启动时自动拉起 Redis + Worker。
# 启动：conda activate cgi_pipeline && python -m dashboard.app

import json
import os
import re
import time
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from core.bootstrap import cfg as _cfg
from core.service_manager import (
    is_redis_alive, start_redis, is_worker_alive, start_worker,
    get_service_status, ensure_ready, shutdown_all, get_celery_app,
    install_exit_hooks,
)
from core.task_status import TERMINAL_STATUSES

PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)
AUDIT_DIR = PROJECT_ROOT / 'audit'
REPORT_DIR = PROJECT_ROOT / 'reports'


# ── 服务自举生命周期 ──
@asynccontextmanager
async def lifespan(app):
    """Dashboard 启动时自动拉起 Redis + Worker，关闭时回收"""
    print('[Dashboard] 正在启动服务...')
    start_redis()
    start_worker('maya')
    print('[Dashboard] 服务就绪')
    yield
    print('[Dashboard] 正在关闭...')
    shutdown_all()
    print('[Dashboard] 已关闭')

install_exit_hooks()

app = FastAPI(title='CGI Pipeline Dashboard', version='2.0', lifespan=lifespan)

# ── 静态文件 ──
DASHBOARD_DIR = Path(__file__).parent
app.mount('/static', StaticFiles(directory=str(DASHBOARD_DIR / 'static')), name='static')


# ── API：获取所有任务 ──
@app.get('/api/tasks')
async def get_tasks():
    """获取所有审计账本中的任务"""
    tasks = []
    if not AUDIT_DIR.exists():
        return {'tasks': tasks}

    for f in sorted(AUDIT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not f.suffix == '.json':
            continue
        try:
            entries = []
            for line in f.read_text(encoding='utf-8').strip().split('\n'):
                if line.strip():
                    entries.append(json.loads(line))
            if entries:
                latest = entries[-1]
                tasks.append({
                    'task_id': latest.get('task_id', f.stem),
                    'skill_id': latest.get('skill_id', ''),
                    'status': latest.get('status', 'UNKNOWN'),
                    'detail': str(latest.get('detail', ''))[:200],
                    'attempt': latest.get('attempt', 0),
                    'timestamp': latest.get('ts', 0),
                    'entries_count': len(entries),
                })
        except Exception:
            continue

    return {'tasks': tasks, 'total': len(tasks)}


# ── API：获取单个任务详情 ──
@app.get('/api/tasks/{task_id}')
async def get_task_detail(task_id: str):
    audit_file = AUDIT_DIR / f'{task_id}.json'
    if not audit_file.exists():
        return {'error': f'Task {task_id} not found'}

    entries = []
    for line in audit_file.read_text(encoding='utf-8').strip().split('\n'):
        if line.strip():
            entries.append(json.loads(line))
    return {'task_id': task_id, 'entries': entries}


# ── API：获取技能列表 ──
@app.get('/api/skills')
async def get_skills():
    from core.skill_registry import get_all_skills
    skills = get_all_skills()
    return {'skills': skills, 'total': len(skills)}


# ── API：项目列表（扫描 config/ 目录） ──
CONFIG_DIR = PROJECT_ROOT / 'config'

@app.get('/api/list_projects')
async def list_projects():
    """返回可用项目列表，供前端下拉选择"""
    if not CONFIG_DIR.exists():
        return {'projects': []}
    configs = list(CONFIG_DIR.glob('*_config.json'))
    projects = [c.stem.replace('_config', '') for c in configs]
    return {'projects': sorted(projects)}


# ── API：Worker 健康检查（前端执行按钮调用） ──
@app.get('/api/worker_status')
async def worker_status_api():
    """前端执行前检查 Worker 是否就绪。不就绪时尝试自动拉起。"""
    ok, err = ensure_ready('maya')
    if not ok:
        return {'online': False, 'error': err, 'count': 0}
    from core.service_manager import get_worker_health
    health = get_worker_health('maya')
    return {
        'online': health.get('state') == 'HEALTHY',
        'count': 1 if health.get('pid_alive') else 0,
        'pid': health.get('pid'),
        'health': health,
    }


# ── API：全部服务状态（状态指示灯） ──
@app.get('/api/service_status')
async def service_status_api():
    """返回所有后端服务的运行状态"""
    status = get_service_status(include_heartbeat=True)
    # 兼容前端 svc-redis / svc-worker 字段
    return {
        'redis': status['redis'],
        'worker': status['worker_maya'],
        'dashboard': status['dashboard'],
    }



# ── API：读取文件内容（预设工作流加载等） ──
@app.get('/api/read_file')
async def read_file(path: str = ''):
    """读取项目目录下的文件内容（仅限 .json/.md/.txt）"""
    if not path:
        return {'error': '缺少 path 参数'}

    target = PROJECT_ROOT / path.replace('\\', '/')
    # 安全检查：不能跳出 PROJECT_ROOT
    try:
        target.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return {'error': '路径越界'}

    if not target.exists() or not target.is_file():
        return {'error': f'文件不存在: {path}'}

    allowed = {'.json', '.md', '.txt', '.yml', '.yaml'}
    if target.suffix.lower() not in allowed:
        return {'error': f'不支持的文件类型: {target.suffix}'}

    try:
        content = target.read_text(encoding='utf-8')
        if target.suffix == '.json':
            return json.loads(content)
        return {'content': content, 'filename': target.name}
    except Exception as e:
        return {'error': f'读取失败: {e}'}


# ── API：项目配置详情（供前端下拉菜单） ──
@app.get('/api/project_config/{project}')
async def get_project_config(project: str):
    """返回指定项目的 categories / stages / pipelines"""
    cfg_file = CONFIG_DIR / f'{project}_config.json'
    if not cfg_file.exists():
        return {'error': f'项目配置 {project}_config.json 不存在'}
    cfg = json.loads(cfg_file.read_text(encoding='utf-8'))
    return {
        'project': project,
        'categories': cfg.get('categories', {}),
        'stages': cfg.get('stages', {}),
        'pipelines': cfg.get('pipelines', {}),
    }


# ── API：资产路径解析 ──
@app.post('/api/resolve_asset')
async def resolve_asset(request: Request):
    """调用 AssetResolver 查找资产最新版本路径"""
    data = await request.json()
    try:
        from core.config_loader import load_project_config
        from core.asset_resolver import AssetResolver
        cfg = load_project_config(data['project'])
        resolver = AssetResolver(cfg)
        result = resolver.resolve_for_mcp(
            category=data.get('category', ''),
            asset_name=data['asset_name'],
            stage=data.get('stage', ''),
            pipeline=data.get('pipeline', ''),
        )
        result['project'] = data['project']
        return result
    except Exception as e:
        import traceback
        return {
            'status': 'ERROR',
            'error': str(e),
            'traceback': traceback.format_exc(),
        }


# ── API：资产列表获取（供下拉选择提示使用） ──
@app.post('/api/list_assets')
async def list_assets(request: Request):
    """根据项目和分类列出可用资产名以供前台 datalist 自动补全使用"""
    data = await request.json()
    try:
        from core.config_loader import load_project_config
        from core.asset_resolver import AssetResolver
        cfg = load_project_config(data['project'])
        resolver = AssetResolver(cfg)
        
        category = data.get('category', 'chr')
        results = resolver.scan_category(category)
        assets = [item['asset'] for item in results]
        
        print(f"!!! GETTING list_assets for {data['project']} {category}: {len(assets)} items !!!")
        return {
            'status': 'SUCCESS',
            'project': data['project'],
            'category': category,
            'assets': sorted(list(set(assets))),
        }
    except Exception as e:
        import traceback
        return {'status': 'ERROR', 'error': str(e)}

# ── API：节点通用预览与参数自动建议 ──
@app.post('/api/preview_node_action')
async def preview_node_action(request: Request):
    data = await request.json()
    skill_id = data.get('skill_id', '')
    source_path = data.get('source_path', '')
    project = data.get('project', '')
    category = data.get('category', '')
    stage = data.get('stage', '')
    node_data = data.get('node_data', {})
    
    if not source_path:
        return {'status': 'ERROR', 'error': 'No source path provided'}

    import os
    preview_html = None
    suggested_params = {}
    
    try:
        # === 1. 构建配置建议 ===
        if project and stage:
            try:
                from core.config_loader import load_project_config
                cfg = load_project_config(project)
                stage_cfg = cfg.get('stages', {}).get(stage, {})
                geom_roots = stage_cfg.get('geom_roots', [])
                if geom_roots:
                    # 倾向于推荐带 cache 或 geo 的
                    cache_idx = next((i for i, v in enumerate(geom_roots) if 'cache' in v.lower()), 0)
                    
                    if skill_id in ['export_abc', 'blender_export_abc', 'export_abc_auto']:
                        suggested_params['root_nodes'] = geom_roots[cache_idx]
                        
                    if skill_id in ['blender_export_abc', 'blender_build_asset_info', 'maya_build_asset_info']:
                        suggested_params['cache_group'] = geom_roots[cache_idx].split('|')[-1]
            except Exception:
                pass

        if skill_id == 'assign_udim_materials':
            if category: suggested_params['category'] = category
            if stage: suggested_params['stage'] = stage

        # === 2. 预测操作输出 ===
        if skill_id in ['maya_export_abc', 'blender_export_abc', 'pipeline_export_abc_auto', 'export_abc', 'export_abc_auto']:
            from core.path_guard import is_protected_path
            src_dir = os.path.dirname(source_path)
            src_stem = os.path.splitext(os.path.basename(source_path))[0]
            candidate = os.path.join(src_dir, src_stem + '.abc')

            if is_protected_path(candidate):
                abc_path = f'{PROJECT_ROOT}/projects/{project or "project"}/<task_sandbox>/.info/{src_stem}.abc'
            else:
                abc_path = candidate
                
            display_path = abc_path.replace('\\', '/')
            preview_html = f"<b>🗂️ 预估输出 ({skill_id}):</b><br>{display_path}"
            
        elif skill_id in ['master_cleanup', 'maya_master_cleanup']:
            mode = node_data.get('mode', 'check')
            preview_html = f"<b>🧹 预估动作:</b> {mode} 模式安全扫描诊断"
            
        elif skill_id == 'save_scene':
            preview_html = f"<b>💾 预估动作:</b> 根据规则创建新场景版本"

        elif skill_id == 'publish_asset':
            # 从上游 resolve_asset 推断所有参数
            if project: suggested_params['project'] = project
            if category: suggested_params['category'] = category
            if stage: suggested_params['stage'] = stage
            # asset_name 从 node_data 或者上游推断
            asset_name = node_data.get('asset_name', '') or data.get('asset_name', '')
            if asset_name: suggested_params['asset_name'] = asset_name
            # 预测版本号
            try:
                from core.config_loader import load_project_config as _lpc
                from core.asset_resolver import AssetResolver as _AR
                _cfg = _lpc(project)
                _r = _AR(_cfg)
                _t = _r._get_primary_task(stage) if stage else '?'
                _pub_dir = _r._resolve_ai_stage_dir(category, asset_name or '?', stage or '?')
                _existing = _r._scan_versions(_pub_dir) if _pub_dir.exists() else []
                _next = max(_existing, default=0) + 1
                preview_html = f"<b>📦 预估发布:</b> v{_next:03d}<br>{project}_{category}_{asset_name}_{stage}_{_t}_v{_next:03d}.ma"
            except Exception:
                preview_html = f"<b>📦 预估发布:</b> 将自动递增版本号发布到 ai_publish_root"

        elif skill_id == 'validate_publish':
            # 从上游推断所有参数
            if project: suggested_params['project'] = project
            if category: suggested_params['category'] = category
            if stage: suggested_params['stage'] = stage
            asset_name = node_data.get('asset_name', '') or data.get('asset_name', '')
            if asset_name: suggested_params['asset_name'] = asset_name
            preview_html = f"<b>🔍 预估 QC:</b> 场景完整性 + Cache组 + Mesh数量 + 未知节点 + 空组 + 命名规范"

        elif skill_id == 'rename_asset':
            if project: suggested_params['project'] = project
            if category: suggested_params['category'] = category
            if stage: suggested_params['stage'] = stage
            asset_name = node_data.get('asset_name', '') or data.get('asset_name', '')
            if asset_name: suggested_params['asset_name'] = asset_name
            if project and category and asset_name and stage:
                try:
                    from core.config_loader import load_project_config as _lpc2
                    from core.asset_resolver import AssetResolver as _AR2
                    _cfg2 = _lpc2(project)
                    _r2 = _AR2(_cfg2)
                    _t2 = _r2._get_primary_task(stage)
                    preview_html = f"<b>📝 预估重命名:</b><br>{project}_{category}_{asset_name}_{stage}_{_t2}_v???.ma"
                except Exception:
                    preview_html = f"<b>📝 预估重命名:</b> 将应用管线标准命名格式"
            else:
                preview_html = f"<b>📝 预估重命名:</b> 将应用管线标准命名格式"

        else:
            preview_html = f"<b>⚙ 预估执行目标:</b><br>{os.path.basename(source_path)}"

        return {'status': 'SUCCESS', 'preview_html': preview_html, 'suggested_params': suggested_params}
    except Exception as e:
        import traceback
        return {'status': 'ERROR', 'error': str(e), 'traceback': traceback.format_exc()}


# ── API：获取系统状态 ──
@app.get('/api/status')
async def get_status():
    ipc_cmd = PROJECT_ROOT / 'ipc' / 'cmd'
    ipc_result = PROJECT_ROOT / 'ipc' / 'result'
    workers = list(ipc_cmd.iterdir()) if ipc_cmd.exists() else []
    return {
        'project_root': str(PROJECT_ROOT),
        'audit_count': len(list(AUDIT_DIR.iterdir())) if AUDIT_DIR.exists() else 0,
        'worker_count': len(workers),
        'workers': [w.name for w in workers],
    }


# ── SSE：实时事件流 ──
@app.get('/api/events')
async def event_stream(request: Request):
    """SSE 实时推送审计事件（行增量追踪）"""
    async def generate():
        # 记录每个文件的已读行数，而非仅记录文件名是否出现
        seen_lines = {}  # {filename: line_count}
        if AUDIT_DIR.exists():
            for f in AUDIT_DIR.iterdir():
                if f.suffix == '.json':
                    try:
                        line_count = len(f.read_text(encoding='utf-8').strip().split('\n'))
                        seen_lines[f.name] = line_count
                    except Exception:
                        seen_lines[f.name] = 0

        while True:
            if await request.is_disconnected():
                break

            if AUDIT_DIR.exists():
                for fpath in sorted(AUDIT_DIR.iterdir()):
                    if fpath.suffix != '.json':
                        continue
                    fname = fpath.name
                    try:
                        lines = fpath.read_text(encoding='utf-8').strip().split('\n')
                        current_count = len(lines)
                        prev_count = seen_lines.get(fname, 0)

                        if current_count > prev_count:
                            # 推送所有新增行
                            new_lines = lines[prev_count:]
                            for line_str in new_lines:
                                try:
                                    entry = json.loads(line_str)
                                    event_data = json.dumps({
                                        'task_id': entry.get('task_id', ''),
                                        'skill_id': entry.get('skill_id', ''),
                                        'status': entry.get('status', ''),
                                        'timestamp': entry.get('ts', 0),
                                        'detail': str(entry.get('detail', ''))[:200],
                                    })
                                    yield f'data: {event_data}\n\n'
                                except (json.JSONDecodeError, Exception):
                                    pass
                            seen_lines[fname] = current_count
                    except Exception:
                        pass

            await _async_sleep(2)

    return StreamingResponse(generate(), media_type='text/event-stream')


async def _async_sleep(seconds):
    await asyncio.sleep(seconds)


# ── 文件夹浏览 API（支持级联下拉） ──
@app.get('/api/browse')
async def browse_directory(path: str = ''):
    """
    返回指定路径下的子文件夹和文件列表。
    用于前端级联下拉选择器实时获取文件夹结构。
    """
    import re

    # 安全防护：路径规范化
    target = Path(path.replace('\\', '/'))
    if not target.is_absolute():
        target = PROJECT_ROOT / 'projects' / path

    # 验证路径有效性
    if not target.exists() or not target.is_dir():
        return {'dirs': [], 'files': [], 'error': f'路径不存在: {path}'}

    dirs = []
    files = []

    try:
        for item in sorted(target.iterdir()):
            if item.name.startswith('.'):
                continue  # 跳过隐藏文件/文件夹
            if item.is_dir():
                dirs.append(item.name)
            elif item.is_file():
                files.append({
                    'name': item.name,
                    'size': item.stat().st_size,
                    'mtime': item.stat().st_mtime,
                })
    except PermissionError:
        return {'dirs': [], 'files': [], 'error': '无权访问'}

    # 文件按版本号降序（v999 > v001），同版本按修改时间降序
    def version_key(f):
        m = re.search(r'_v(\d+)', f['name'])
        ver = int(m.group(1)) if m else 0
        return (-ver, -f['mtime'])

    files.sort(key=version_key)

    return {
        'path': str(target).replace('\\', '/'),
        'dirs': dirs,
        'files': files,
    }


# ── 主页（卡片式） ──
@app.get('/', response_class=HTMLResponse)
async def index():
    html_path = DASHBOARD_DIR / 'static' / 'index.html'
    return HTMLResponse(html_path.read_text(encoding='utf-8'))


# ── 报告查看器页面 ──
@app.get('/report', response_class=HTMLResponse)
async def report_page():
    html_path = DASHBOARD_DIR / 'static' / 'report_viewer.html'
    return HTMLResponse(html_path.read_text(encoding='utf-8'))


# ── 节点编辑器页面 ──
@app.get('/nodes', response_class=HTMLResponse)
async def node_editor():
    html_path = DASHBOARD_DIR / 'static' / 'node_editor.html'
    return HTMLResponse(html_path.read_text(encoding='utf-8'))


# ── API：从节点编辑器执行技能 ──
@app.post('/api/execute_skill')
async def execute_skill(request: Request):
    """
    接收节点编辑器的技能执行请求。
    优先通过 Celery 派发到 DCC Worker，Redis 不可用时降级为仅写审计账本。
    """
    import uuid
    body = await request.json()
    task_id = str(uuid.uuid4())[:8]
    skill_id = body.get('skill_id', 'unknown')
    project = body.get('project', 'default')
    asset_name = body.get('asset_name', 'unknown')
    source_path = body.get('source_path', '')
    parameters = body.get('parameters', {})

    payload = {
        'task_id': task_id,
        'skill_id': skill_id,
        'project': project,
        'asset_name': asset_name,
        'source_path': source_path,
        'parameters': parameters,
    }

    dispatched = False
    celery_task_id = None

    # 尝试通过 Celery 派发
    try:
        _app = get_celery_app()
        result = _app.send_task(
            'core.tasks.execute_dcc_skill',
            args=[payload],
            task_id=task_id,
        )
        celery_task_id = result.id
        dispatched = True
    except Exception as e:
        # Redis 不可用 — 降级为仅审计
        detail = f'Celery 派发失败（{type(e).__name__}: {e}），已降级为本地审计'

        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        audit_file = AUDIT_DIR / f'{task_id}.json'
        entry = {
            'task_id': task_id,
            'skill_id': skill_id,
            'status': 'DEGRADED',
            'detail': detail,
            'attempt': 1,
            'ts': time.time(),
        }
        with open(audit_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return {
        'task_id': celery_task_id or task_id,
        'status': 'DISPATCHED' if dispatched else 'DEGRADED',
        'skill_id': skill_id,
        'dispatched': dispatched,
    }


# ── API：图级执行（ComfyUI 风格）——一次性提交整张节点图 ──
@app.post('/api/execute_graph')
async def execute_graph(request: Request):
    """
    接收前端提交的完整节点图，提取技能链后一次性派发到 Celery。
    与 /api/execute_skill（逐技能）相比：
    - 1 次 API 调用 vs N 次
    - 1 次 Celery 任务 vs N 次
    - Maya 只打开文件 1 次
    """
    import uuid
    body = await request.json()
    chain_task_id = f'chain-{str(uuid.uuid4())[:8]}'

    skill_chain = body.get('skill_chain', [])
    if not skill_chain:
        return {'error': '技能链为空', 'dispatched': False}

    payload = {
        'task_id': chain_task_id,
        'source_path': body.get('source_path', ''),
        'project': body.get('project', 'default'),
        'asset_name': body.get('asset_name', 'untitled'),
        'category': body.get('category', ''),
        'skill_chain': skill_chain,
    }

    dispatched = False
    try:
        _app = get_celery_app()
        _app.send_task(
            'core.tasks.execute_skill_chain',
            args=[payload],
            task_id=chain_task_id,
        )
        dispatched = True
    except Exception as e:
        # Redis 不可用 — 降级
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        audit_file = AUDIT_DIR / f'{chain_task_id}.json'
        entry = {
            'task_id': chain_task_id,
            'skill_id': 'chain',
            'status': 'DEGRADED',
            'detail': f'Celery 派发失败: {e}',
            'ts': time.time(),
        }
        with open(audit_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return {
        'task_id': chain_task_id,
        'dispatched': dispatched,
        'skill_count': len(skill_chain),
    }

# ── API：轮询任务状态（SSE 流）──
@app.get('/api/tasks/{task_id}/poll')
async def poll_task(task_id: str, request: Request):
    """SSE 流式轮询任务直到完成或超时 — 发送所有新 entry，非仅最后一条"""
    async def generate():
        timeout = 300  # 5 分钟超时
        start = time.time()
        seen_count = 0  # 已发送过的 entry 数量

        while time.time() - start < timeout:
            if await request.is_disconnected():
                break

            audit_file = AUDIT_DIR / f'{task_id}.json'
            if audit_file.exists():
                entries = []
                for line in audit_file.read_text(encoding='utf-8').strip().split('\n'):
                    if line.strip():
                        entries.append(json.loads(line))

                # 只发送新的条目
                new_entries = entries[seen_count:]
                if new_entries:
                    seen_count = len(entries)
                    for entry in new_entries:
                        event_data = json.dumps({
                            'task_id': task_id,
                            'status': entry.get('status', 'STARTED'),
                            'skill_id': entry.get('skill_id', ''),
                            'detail': str(entry.get('detail', ''))[:500],
                            'timestamp': entry.get('ts', 0),
                            'step': entry.get('step', -1),
                            'total_steps': entry.get('total_steps', 0),
                        }, ensure_ascii=False)
                        yield f'data: {event_data}\n\n'

                    # 检查最后一条是否终止状态
                    last_status = entries[-1].get('status', '')
                    if last_status in TERMINAL_STATUSES:
                        return  # 任务结束

            await asyncio.sleep(2)  # 2秒检查间隔（比原来3秒更及时）

        # 超时
        yield f'data: {json.dumps({"task_id": task_id, "status": "TIMEOUT"})}\n\n'

    return StreamingResponse(generate(), media_type='text/event-stream')


# ── API：取消任务 ──
@app.post('/api/tasks/{task_id}/cancel')
async def cancel_task(task_id: str):
    """通过 Celery revoke 取消正在执行的任务"""
    try:
        _app = get_celery_app()
        _app.control.revoke(task_id, terminate=True, signal='SIGTERM')

        # 写入审计日志
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        audit_file = AUDIT_DIR / f'{task_id}.json'
        entry = {
            'task_id': task_id,
            'status': 'CANCELLED',
            'detail': '用户手动取消',
            'ts': time.time(),
        }
        with open(audit_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

        return {'cancelled': True, 'task_id': task_id}
    except Exception as e:
        return {'cancelled': False, 'error': str(e)}


# ── API：生成报告（读取已由后端生成的报告路径）──
@app.post('/api/reports/generate')
async def generate_report_api(request: Request):
    """读取已完成任务产生的 receipt，从中提取 report_path 并直接返回"""
    import time as _time
    data = await request.json()
    task_id = data.get('task_id', '')
    chain_results = data.get('chain_results', [])

    if not task_id and not chain_results:
        return {'status': 'error', 'error': '需要 task_id 或 chain_results'}

    report_path = ""

    # 1. 尝试从传入的 chain_results 提取
    if chain_results:
        # 取最后一个有效 result 找 report_path
        for cr in reversed(chain_results):
            if isinstance(cr, dict) and cr.get('report_path'):
                report_path = cr.get('report_path')
                break
            detail_str = cr.get('detail', '')
            try:
                receipt = json.loads(detail_str) if isinstance(detail_str, str) else detail_str
                if isinstance(receipt, dict):
                    report_path = receipt.get('report_path') or receipt.get('outputs', {}).get('report_path', '')
                    if report_path:
                        break
            except Exception:
                pass

    # 2. 如果没找到，从 audit log 文件读取最后一个 entry
    if not report_path and task_id:
        audit_file = AUDIT_DIR / f'{task_id}.json'
        if audit_file.exists():
            for line in reversed(audit_file.read_text(encoding='utf-8').splitlines()):
                try:
                    entry = json.loads(line)
                    detail_str = entry.get('detail', '')
                    receipt = json.loads(detail_str) if isinstance(detail_str, str) else detail_str
                    if isinstance(receipt, dict):
                        report_path = receipt.get('report_path') or receipt.get('outputs', {}).get('report_path', '')
                        if report_path:
                            break
                except Exception:
                    pass

    if not report_path:
        return {'status': 'ok', 'run_id': task_id, 'message': '未找到 report_path（任务可能未产出 MD 报告）'}

    return {'status': 'ok', 'run_id': task_id, 'report_path': report_path}


# ── API：列出所有报告 ──
@app.get('/api/reports')
async def list_reports():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    reports = []
    for f in sorted(REPORT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix == '.md':
            reports.append({
                'run_id': f.stem,
                'filename': f.name,
                'size': f.stat().st_size,
                'modified': f.stat().st_mtime,
            })
    return {'reports': reports, 'total': len(reports)}


# ── API：获取单个报告 MD 内容 ──
@app.get('/api/reports/{run_id}')
async def get_report(run_id: str):
    filepath = REPORT_DIR / f'{run_id}.md'
    if not filepath.exists():
        return {'error': f'报告 {run_id} 不存在'}
    content = filepath.read_text(encoding='utf-8')
    return {'run_id': run_id, 'content': content}


# ── 启动入口 ──
if __name__ == '__main__':
    import uvicorn
    port = int(os.getenv('DASHBOARD_PORT', '9000'))
    print(f'[Dashboard] CGI Pipeline Dashboard: http://localhost:{port}')
    print(f'[Dashboard] 节点编辑器:             http://localhost:{port}/nodes')
    print(f'[Dashboard] 报告查看器:             http://localhost:{port}/report')
    uvicorn.run(app, host='0.0.0.0', port=port)

