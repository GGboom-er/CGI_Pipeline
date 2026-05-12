"""验证 asset_resolver 新配置结构的路径解析"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['PROJECT_ROOT'] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from core.config_loader import load_project_config, reload_config, get_source_root, get_server_root, get_path_root
from core.asset_resolver import AssetResolver

reload_config('ysj')
cfg = load_project_config('ysj')
resolver = AssetResolver(cfg)

print('=== config_loader ===')
print(f'server_root: {get_server_root("ysj")}')
print(f'source_root (compat): {get_source_root("ysj")}')
print(f'path_root assets: {get_path_root("ysj", "assets")}')
print(f'path_root asset_lib: {get_path_root("ysj", "asset_lib")}')
print(f'path_root shots: {get_path_root("ysj", "shots")}')

print('\n=== asset resolver: rig stage ===')
rig_dir = resolver._resolve_stage_dir('chr', 'yjTie', 'rig')
print(f'rig stage_dir: {rig_dir}')
assert 'pub/assets/chr/yjTie/rig/rigMaster' in str(rig_dir).replace('\\', '/'), f'FAIL: {rig_dir}'
print('[PASS] rig 路径正确')

print('\n=== asset resolver: lib stage (asset_lib) ===')
lib_dir = resolver._resolve_stage_dir('chr', 'yjTie', 'lib')
print(f'lib stage_dir: {lib_dir}')
assert 'pub/asset_lib/chr/yjTie/lib/libMaster' in str(lib_dir).replace('\\', '/'), f'FAIL: {lib_dir}'
print('[PASS] lib 路径走 asset_lib')

print('\n=== asset resolver: mod stage ===')
mod_dir = resolver._resolve_stage_dir('chr', 'yjTie', 'mod')
print(f'mod stage_dir: {mod_dir}')
assert 'pub/assets/chr/yjTie/mod/modMaster' in str(mod_dir).replace('\\', '/'), f'FAIL: {mod_dir}'
print('[PASS] mod 路径正确')

print('\n=== asset resolver: primary_task ===')
assert resolver._get_primary_task('rig') == 'rigMaster'
assert resolver._get_primary_task('lib') == 'libMaster'
assert resolver._get_primary_task('tex') == 'texMaster'
assert resolver._get_primary_task('lybg') == 'lybgMaster'
print('[PASS] primary_task 全部正确')

print('\n=== asset resolver: AI publish path ===')
ai_rig = resolver._resolve_ai_stage_dir('chr', 'yjTie', 'rig')
print(f'ai rig dir: {ai_rig}')
assert '/runs/assets/chr/yjTie/rig/rigMaster' in str(ai_rig).replace('\\', '/')
ai_lib = resolver._resolve_ai_stage_dir('chr', 'yjTie', 'lib')
print(f'ai lib dir: {ai_lib}')
assert '/runs/asset_lib/chr/yjTie/lib/libMaster' in str(ai_lib).replace('\\', '/')
print('[PASS] AI publish 路径正确')

print('\n=== shot resolver ===')
shot_dir = resolver._resolve_shot_dir('sc01', 'cam001', 'ly')
print(f'shot ly dir: {shot_dir}')
assert 'pub/shots/sc01/cam001/ly/lyMaster' in str(shot_dir).replace('\\', '/'), f'FAIL: {shot_dir}'
print('[PASS] shot ly 路径正确')

shot_lgt = resolver._resolve_shot_dir('sc01', 'cam001', 'lgt')
print(f'shot lgt dir: {shot_lgt}')
assert 'lgt/sc01/cam001/lgtMaster' in str(shot_lgt).replace('\\', '/'), f'FAIL: {shot_lgt}'
print('[PASS] shot lgt 路径走 lgt root + path_pattern')

print('\n=== stage_info / category_stages / shot_stages ===')
rig_info = resolver.get_stage_info('rig')
assert rig_info['primary_task'] == 'rigMaster'
assert 'bs' in rig_info.get('sub_tasks', {})
print(f'rig stage_info: primary={rig_info["primary_task"]}, subs={list(rig_info.get("sub_tasks",{}).keys())}')

chr_stages = resolver.get_category_stages('chr')
assert chr_stages == ['mod', 'uv', 'tex', 'lyrig', 'rig', 'lib'], f'FAIL: {chr_stages}'
prp_stages = resolver.get_category_stages('prp')
assert 'lybg' in prp_stages
print(f'chr stages: {chr_stages}')
print(f'prp stages: {prp_stages}')

shot_stages = resolver.get_shot_stages()
assert 'ly' in shot_stages
assert 'lgt' in shot_stages
print(f'shot stages: {list(shot_stages.keys())}')
print('[PASS] 元数据查询全部正确')

print('\n=== path_guard: suggest_ai_publish_path ===')
from core.path_guard import suggest_ai_publish_path
src_assets = cfg['server_root'] + '/pub/assets/chr/yjTie/rig/rigMaster/ysj_chr_yjTie_rig_rigMaster_v007.ma'
ai_path = suggest_ai_publish_path(src_assets)
print(f'assets -> ai: {ai_path}')
assert ai_path and '/runs/' in ai_path and '/assets/' in ai_path, f'FAIL: {ai_path}'

src_lib = cfg['server_root'] + '/pub/asset_lib/chr/yjTie/lib/libMaster/ysj_chr_yjTie_lib_libMaster_v001.ma'
ai_lib_path = suggest_ai_publish_path(src_lib)
print(f'asset_lib -> ai: {ai_lib_path}')
assert ai_lib_path and '/runs/' in ai_lib_path and '/asset_lib/' in ai_lib_path, f'FAIL: {ai_lib_path}'

src_shot = cfg['server_root'] + '/pub/shots/sc01/cam001/ly/lyMaster/ysj_sc01_cam001_ly_lyMaster_v001.ma'
ai_shot_path = suggest_ai_publish_path(src_shot)
print(f'shots -> ai: {ai_shot_path}')
assert ai_shot_path and '/runs/' in ai_shot_path and '/shots/' in ai_shot_path, f'FAIL: {ai_shot_path}'
print('[PASS] path_guard 多 root 映射全部正确')

print('\n========== ALL TESTS PASSED ==========')
