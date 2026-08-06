# cli.py
# CGI Pipeline CLI — 脱离 AI 直接在命令行运行 API/工作流
#
# 用法：
#   python cli.py list-apis
#   python cli.py list-workflows
#   python cli.py resolve-asset --project ysj --asset xiaotianquan
#   python cli.py run-api maya.rig.maya_clean_skinweights --project ysj --asset xiaotianquan --source-path xxx.ma
#   python cli.py run-api maya.asset.maya_master_cleanup --project ysj --asset xiaotianquan --source-path xxx.ma --param mode=check
#   python cli.py run-chain --project ysj --asset xiaotianquan --source-path xxx.ma --steps maya.rig.maya_clean_skinweights,maya.asset.save_scene --param save_path=yyy.ma
#   python cli.py run-workflow tex_to_rig_verify_and_sync --project ysj --asset ciweiguai

import argparse
import json
import os
import sys
import time
import uuid

from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(os.getenv('PROJECT_ROOT', '.'))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


_WORKFLOW_WAIT_TERMINAL_STATUSES = {
    'SUCCESS',
    'ERROR',
    'TIMEOUT',
    'BLOCKED',
    'AUDIT_FAILED',
    'NEEDS_ATTENTION',
    'WORKFLOW_SUCCESS',
    'WORKFLOW_ABORTED',
    'WORKFLOW_ERROR',
    'WORKFLOW_AUDIT_FAILED',
    'CHAIN_ABORTED',
    'CHAIN_BLOCKED',
    'CHAIN_ERROR',
    'CHAIN_AUDIT_FAILED',
}


def _is_workflow_wait_terminal(status: str) -> bool:
    return str(status or '').upper() in _WORKFLOW_WAIT_TERMINAL_STATUSES


def cmd_list_apis(args):
    from api.registry import list_apis

    apis = list_apis(
        dcc=args.dcc or None,
        domain=args.domain or None,
        tier=args.tier or None,
    )
    if args.json:
        print(json.dumps(apis, ensure_ascii=False, indent=2))
        return

    print(f'已注册 {len(apis)} 个 API:\n')
    for api in apis:
        modes = ','.join(api.get('execution_modes', []))
        print(
            f"  {api['api_id']:36s} "
            f"[{api.get('dcc', '?'):8s}] "
            f"[{api.get('domain', '?'):10s}] "
            f"[{api.get('tier', '?'):11s}] "
            f"[{modes}]"
        )
        if args.verbose:
            print(f"    {api.get('help', {}).get('summary', '')}")


def cmd_api_help(args):
    from api.registry import api_help

    print(json.dumps(api_help(args.api_id), ensure_ascii=False, indent=2))


def cmd_list_workflows(args):
    from core.workflow_engine import list_workflows
    workflows = list_workflows()
    print(f'已注册 {len(workflows)} 个工作流:\n')
    for wf in workflows:
        print(f"  {wf['workflow_id']:30s} {wf.get('name','')}")
        print(f"    步骤: {' → '.join(wf.get('steps', []))}")


def cmd_resolve_asset(args):
    from core.config_loader import load_project_config
    from core.asset_resolver import AssetResolver
    cfg = load_project_config(args.project)
    resolver = AssetResolver(cfg)

    if args.stage:
        latest = resolver.resolve_latest(args.category, args.asset, args.stage)
        versions = resolver.list_versions(args.category, args.asset, args.stage)
        print(f'项目: {args.project}  资产: {args.asset}  阶段: {args.stage}')
        print(f'最新版本: {latest}')
        print(f'所有版本: {versions}')
    else:
        resolved = resolver.resolve_by_stage(args.category, args.asset)
        if resolved:
            print(f'项目: {args.project}  资产: {args.asset}')
            for k, v in resolved.items():
                print(f'  {k}: {v}')
        else:
            print(f'未找到资产: {args.project}/{args.category}/{args.asset}')


def _parse_params(param_list: list[str]) -> dict:
    """解析 --param key=value 参数"""
    params = {}
    for p in (param_list or []):
        if '=' in p:
            k, v = p.split('=', 1)
            if v.lower() in ('true', 'false'):
                v = v.lower() == 'true'
            else:
                try:
                    v = float(v) if '.' in v else int(v)
                except ValueError:
                    pass
            params[k] = v
    return params


def cmd_run_api(args):
    from core.dcc_factory import create_worker, open_source_file
    from core.api_registry import get_api_dcc, resolve_api_id

    api_id = resolve_api_id(args.api_id)
    dcc_type = get_api_dcc(api_id)
    task_id = f"cli-{uuid.uuid4().hex[:8]}"
    params = _parse_params(args.param)

    payload = {
        'task_id': task_id,
        'api_id': api_id,
        'project': args.project,
        'asset_name': args.asset,
        'source_path': args.source_path or '',
        'parameters': params,
    }

    print(f'[{task_id}] 启动 {dcc_type} Worker...')
    worker = create_worker(dcc_type, args.source_path)
    worker.start()

    try:
        if args.source_path:
            print(f'[{task_id}] 打开源文件: {args.source_path}')
            ok, err = open_source_file(worker, task_id, args.source_path, dcc_type)
            if not ok:
                print(f'[{task_id}] 打开失败: {err}')
                return

        print(f'[{task_id}] 执行 API: {api_id}')
        t0 = time.time()
        result = worker.run_api(payload)
        elapsed = round(time.time() - t0, 2)

        status = result.get('status', 'UNKNOWN')
        print(f'[{task_id}] {status} ({elapsed}s)')

        detail = result.get('detail', '')
        if detail:
            try:
                detail_obj = json.loads(detail)
                print(json.dumps(detail_obj, indent=2, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError):
                print(detail)
    finally:
        worker.shutdown()
        print(f'[{task_id}] Worker 已关闭')


def cmd_run_chain(args):
    from core.dcc_factory import create_worker, open_source_file
    from core.api_registry import get_api_dcc, resolve_api_id

    steps = [resolve_api_id(s.strip()) for s in args.steps.split(',') if s.strip()]
    if not steps:
        print('错误: --steps 不能为空')
        return

    dcc_type = get_api_dcc(steps[0])
    task_id = f"cli-chain-{uuid.uuid4().hex[:8]}"
    params = _parse_params(args.param)

    print(f'[{task_id}] 链式执行: {" → ".join(steps)}')
    worker = create_worker(dcc_type)
    worker.start()

    try:
        if args.source_path:
            print(f'[{task_id}] 打开源文件: {args.source_path}')
            ok, err = open_source_file(worker, task_id, args.source_path, dcc_type)
            if not ok:
                print(f'[{task_id}] 打开失败: {err}')
                return

        for i, api_id in enumerate(steps):
            step_params = dict(params) if api_id == steps[-1] else {}
            payload = {
                'task_id': f'{task_id}-step{i}',
                'api_id': api_id,
                'project': args.project,
                'asset_name': args.asset,
                'source_path': '',
                'parameters': step_params,
            }
            print(f'[{task_id}] Step {i+1}/{len(steps)}: {api_id}')
            t0 = time.time()
            result = worker.run_api(payload)
            elapsed = round(time.time() - t0, 2)
            status = result.get('status', 'UNKNOWN')
            print(f'  → {status} ({elapsed}s)')

            detail = result.get('detail', '')
            if detail:
                try:
                    detail_obj = json.loads(detail)
                    if detail_obj.get('status') in ('ERROR', 'BLOCKED', 'NEEDS_ATTENTION'):
                        print(f'  链中断: {detail_obj.get("error", detail_obj.get("status"))}')
                        return
                except (json.JSONDecodeError, TypeError):
                    pass

        print(f'[{task_id}] 链式执行完成')
    finally:
        worker.shutdown()
        print(f'[{task_id}] Worker 已关闭')


def cmd_run_workflow(args):
    from mcp_server.internals import _read_audit, _submit_workflow

    extra_params = _parse_params(args.param)
    if args.rig_path:
        extra_params['rig_path'] = args.rig_path
    if args.category:
        extra_params['category'] = args.category

    payload = {
        'workflow_id': args.workflow_id,
        'source_path': args.source_path or '',
        'project': args.project,
        'asset_name': args.asset,
        'extra_params': extra_params,
    }
    result = _submit_workflow(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not args.wait or result.get('status') != 'SUBMITTED':
        return

    task_id = result.get('task_id')
    while True:
        time.sleep(args.interval)
        state = _read_audit(task_id)
        print(json.dumps(state, ensure_ascii=False))
        if _is_workflow_wait_terminal(state.get('status')):
            break


def main():
    parser = argparse.ArgumentParser(
        description='CGI Pipeline CLI — 脱离 AI 直接运行 API 和工作流',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', help='可用命令')

    # list-apis
    p_la = sub.add_parser('list-apis', help='列出已注册 API')
    p_la.add_argument('--dcc', default='', choices=['', 'maya', 'blender', 'ue', 'pipeline'])
    p_la.add_argument('--domain', default='', choices=['', 'asset', 'rig', 'animation', 'pipeline'])
    p_la.add_argument('--tier', default='', choices=['', 'read', 'write', 'destructive'])
    p_la.add_argument('-v', '--verbose', action='store_true', help='显示 API 用途')
    p_la.add_argument('--json', action='store_true', help='输出机器可读 JSON')

    # api-help
    p_ah = sub.add_parser('api-help', help='查看 API 的 Catalog 契约')
    p_ah.add_argument('api_id', help='API ID')

    # list-workflows
    sub.add_parser('list-workflows', help='列出所有已注册工作流')

    # resolve-asset
    p_ra = sub.add_parser('resolve-asset', help='查询资产路径和版本')
    p_ra.add_argument('--project', required=True)
    p_ra.add_argument('--asset', required=True)
    p_ra.add_argument('--category', default='chr')
    p_ra.add_argument('--stage', default='')

    # run-api
    p_ra = sub.add_parser('run-api', help='执行单个 API')
    p_ra.add_argument('api_id', help='API ID，可先用 api-help 查看参数')
    p_ra.add_argument('--project', default='default')
    p_ra.add_argument('--asset', default='untitled')
    p_ra.add_argument('--source-path', default='')
    p_ra.add_argument('--param', action='append', help='API 参数 key=value，可多次使用')

    # run-chain
    p_rc = sub.add_parser('run-chain', help='链式执行多个 API')
    p_rc.add_argument('--steps', required=True, help='逗号分隔的 API ID 列表')
    p_rc.add_argument('--project', default='default')
    p_rc.add_argument('--asset', default='untitled')
    p_rc.add_argument('--source-path', default='')
    p_rc.add_argument('--param', action='append', help='传给最后一步的参数 key=value')

    # run-workflow
    p_rw = sub.add_parser('run-workflow', help='提交预定义工作流')
    p_rw.add_argument('workflow_id', help='工作流 ID')
    p_rw.add_argument('--project', default='default')
    p_rw.add_argument('--asset', default='untitled')
    p_rw.add_argument('--category', default='chr')
    p_rw.add_argument('--source-path', default='', help='可选 source 文件路径；为空时由 workflow 解析节点按资产名查找')
    p_rw.add_argument('--rig-path', default='', help='可选 rig 文件路径；为空时由 workflow 解析节点按资产名查找')
    p_rw.add_argument('--param', action='append', help='额外 input 参数 key=value，可多次使用')
    p_rw.add_argument('--wait', action='store_true', help='提交后轮询任务状态')
    p_rw.add_argument('--interval', type=float, default=5.0, help='--wait 轮询间隔秒数')

    args = parser.parse_args()

    if args.command == 'list-apis':
        cmd_list_apis(args)
    elif args.command == 'api-help':
        cmd_api_help(args)
    elif args.command == 'list-workflows':
        cmd_list_workflows(args)
    elif args.command == 'resolve-asset':
        cmd_resolve_asset(args)
    elif args.command == 'run-api':
        cmd_run_api(args)
    elif args.command == 'run-chain':
        cmd_run_chain(args)
    elif args.command == 'run-workflow':
        cmd_run_workflow(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
