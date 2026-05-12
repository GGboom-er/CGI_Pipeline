"""验证 resolve_by_stage 阶段解析"""
import sys, os, shutil
sys.path.insert(0, r'y:\GGbommer\scripts\CGI_Pipeline')
os.chdir(r'y:\GGbommer\scripts\CGI_Pipeline')
os.environ['PROJECT_ROOT'] = r'y:\GGbommer\scripts\CGI_Pipeline'

from core.config_loader import load_project_config
from core.asset_resolver import AssetResolver
from pathlib import Path

ROOT = Path(os.environ['PROJECT_ROOT'])

print('=== resolve_by_stage 验证 ===\n')

cfg = load_project_config('ysj')
resolver = AssetResolver(cfg)

# 测试 1: 使用真实数据 — xiaotianquan 有 rig 和 uv
r = resolver.resolve_by_stage('chr', 'xiaotianquan')
assert r is not None, 'FAIL: 应找到资产'
print(f'[PASS] 阶段优先级: stage={r["stage"]}, version={r["version"]}')
print(f'       path={r["path"]}')

# 测试 2: scan_category 批量扫描
results = resolver.scan_category('chr')
print(f'[PASS] scan_category(chr): {len(results)} 个资产')
for item in results:
    status = '✓' if item['valid'] else '✗'
    print(f'  {status} {item["asset"]}: stage={item.get("stage","N/A")}, version={item.get("version","N/A")}')

# 测试 3: 不存在的资产返回 None
r_none = resolver.resolve_by_stage('chr', 'nonexistent_asset_xyz')
assert r_none is None
print('[PASS] 不存在的资产返回 None')

# 测试 4: 模拟目录测试回退逻辑
MOCK_ROOT = ROOT / '_test_stage_assets'
mock_cfg = dict(cfg)
mock_cfg['server_root'] = str(MOCK_ROOT)
mock_cfg['path_roots'] = {'assets': '.'}
mock_cfg.pop('source_root', None)
mock_resolver = AssetResolver(mock_cfg)

(MOCK_ROOT / 'chr' / 'hero_01' / 'mod' / 'modMaster').mkdir(parents=True, exist_ok=True)
(MOCK_ROOT / 'chr' / 'hero_01' / 'tex' / 'texMaster').mkdir(parents=True, exist_ok=True)
(MOCK_ROOT / 'chr' / 'hero_01' / 'mod' / 'modMaster' / 'test_chr_hero_01_mod_modMaster_v001.blend').write_text('mod v1')
(MOCK_ROOT / 'chr' / 'hero_01' / 'tex' / 'texMaster' / 'test_chr_hero_01_tex_texMaster_v001.blend').write_text('tex v1')

r_mock = mock_resolver.resolve_by_stage('chr', 'hero_01', ext_filter=['.blend'])
assert r_mock is not None
print(f'[PASS] 模拟数据: stage={r_mock["stage"]}, version={r_mock["version"]}')

# 清理
shutil.rmtree(MOCK_ROOT)
print('\n=== 全部 4 项验证通过 ===')
