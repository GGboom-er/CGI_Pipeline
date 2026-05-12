"""验证 AssetResolver（统一路径系统）"""
import sys, os
sys.path.insert(0, r'y:\GGbommer\scripts\CGI_Pipeline')
os.chdir(r'y:\GGbommer\scripts\CGI_Pipeline')
os.environ['PROJECT_ROOT'] = r'y:\GGbommer\scripts\CGI_Pipeline'

from core.config_loader import load_project_config
from core.asset_resolver import AssetResolver
from pathlib import Path

print('=== AssetResolver 统一路径验证 ===\n')

cfg = load_project_config('ysj')
resolver = AssetResolver(cfg)
print(f'[PASS] 配置加载: source_root={resolver.source_root}')
print(f'       ai_publish_root={resolver.ai_publish_root}')

# 测试 1：定位 rig 阶段最新版本
latest = resolver.resolve_latest('chr', 'xiaotianquan', 'rig')
assert latest is not None, 'FAIL: 应找到 rig 最新版本'
assert 'v003' in latest.name, f'FAIL: 应为 v003, got {latest.name}'
print(f'[PASS] resolve_latest(rig): {latest.name}')

# 测试 2：定位 uv 阶段
latest_uv = resolver.resolve_latest('chr', 'xiaotianquan', 'uv')
assert latest_uv is not None, 'FAIL: 应找到 uv 版本'
assert 'v001' in latest_uv.name, f'FAIL: 应为 v001, got {latest_uv.name}'
print(f'[PASS] resolve_latest(uv): {latest_uv.name}')

# 测试 3：按阶段优先级自动选择
resolved = resolver.resolve_by_stage('chr', 'xiaotianquan')
assert resolved is not None, 'FAIL: 应找到资产'
print(f'[PASS] resolve_by_stage: stage={resolved["stage"]}, version={resolved["version"]}')

# 测试 4：版本列表
versions = resolver.list_versions('chr', 'xiaotianquan', 'rig')
assert len(versions) == 3, f'FAIL: 应有 3 个版本, got {len(versions)}'
print(f'[PASS] list_versions(rig): {[v["version"] for v in versions]}')

# 测试 5：AI 发布路径
next_ver, pub_path = resolver.resolve_next_publish('chr', 'xiaotianquan', 'rig')
assert next_ver >= 1, f'FAIL: 版本号应 >= 1, got {next_ver}'
print(f'[PASS] resolve_next_publish: v{next_ver:03d} → {pub_path}')

# 测试 6：文件名解析
parsed = AssetResolver.parse_filename('ysj_chr_xiaotianquan_rig_rigMaster_v003.ma')
assert parsed is not None, 'FAIL: 应能解析文件名'
assert parsed['project'] == 'ysj'
assert parsed['category'] == 'chr'
assert parsed['asset'] == 'xiaotianquan'
assert parsed['stage'] == 'rig'
print(f'[PASS] parse_filename: {parsed}')

# 测试 7：路径解析
path_info = resolver.parse_path(str(latest))
assert path_info is not None, 'FAIL: 应能解析路径'
print(f'[PASS] parse_path: {path_info}')

print('\n=== 全部 7 项验证通过 ===')
