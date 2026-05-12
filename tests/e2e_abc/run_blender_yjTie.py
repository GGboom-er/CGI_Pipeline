# 测试拆分后的原子技能：先 export_abc，再 build_asset_info
import sys, os, json
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'skills'))

from core.config_loader import load_project_config

cfg = load_project_config('ysj')
tex_group = cfg.get('stages', {}).get('tex', {}).get('geom_roots', ['cache'])[0]

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), 'output')).replace('\\', '/')
os.makedirs(OUT, exist_ok=True)
ABC_PATH = OUT + '/ysj_chr_yjTie_tex_texMaster_v007.abc'
INFO_PATH = OUT + '/ysj_chr_yjTie_tex_texMaster_v007_info.json'

# ── 1. 导出 ABC ──
print("=" * 60)
print("STEP 1: blender_export_abc")
from blender_export_abc import execute as export_abc
r1 = export_abc({'parameters': {'abc_path': ABC_PATH, 'cache_group': tex_group}})
print(json.dumps(r1, indent=2, ensure_ascii=False, default=str))

# ── 2. 采集 _info.json ──
print("\n" + "=" * 60)
print("STEP 2: blender_build_asset_info")
from blender_build_asset_info import execute as build_info
r2 = build_info({'parameters': {'info_path': INFO_PATH, 'cache_group': tex_group}})
print(json.dumps(r2, indent=2, ensure_ascii=False, default=str))

print("\n" + "=" * 60)
print(f"ABC: {r1.get('status')} | INFO: {r2.get('status')}")
print("=" * 60)
