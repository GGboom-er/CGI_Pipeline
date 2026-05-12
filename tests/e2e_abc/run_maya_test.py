# tests/e2e_abc/run_maya_test.py
# mayapy E2E 测试: import_abc + assign_udim_materials + save_scene
# 使用 projects/ysj 正式项目路径
import sys, os, json

import maya.standalone
maya.standalone.initialize(name='python')
import maya.cmds as cmds

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'skills'))

# ── 路径配置（使用 projects/ysj 的标准结构）──
PROJ_ROOT = os.path.join(PROJECT_ROOT, 'projects', 'ysj').replace('\\', '/')
ABC_PATH = f"{PROJ_ROOT}/pub/assets/chr/hamaguai/tex/paintTex/ysj_chr_hamaguai_tex_paintTex_v001.abc"
TEXMAP_PATH = f"{PROJ_ROOT}/pub/assets/chr/hamaguai/tex/paintTex/ysj_chr_hamaguai_tex_paintTex_v001_texmap.json"
SRCIMG_ROOT = 'X:/Project/ysj/sourceimages'
SAVE_PATH = f"{PROJ_ROOT}/runs/assets/chr/hamaguai/tex/paintTex/ysj_chr_hamaguai_tex_paintTex_v001.ma"

print("=" * 60)
print("MAYA E2E TEST (projects/ysj)")
print(f"ABC: {ABC_PATH}")
print(f"Save: {SAVE_PATH}")
print("=" * 60)

# ── Step 1: import_abc ──
print("\n>>> Step 1: import_abc")
from import_abc import execute as import_abc_execute
result1 = import_abc_execute({
    'asset_name': 'hamaguai', 'project': 'ysj',
    'parameters': {'abc_path': ABC_PATH, 'scale_factor': 100.0, 'new_scene': True}
})
print(json.dumps(result1, indent=2, ensure_ascii=False, default=str))
assert result1['status'] == 'SUCCESS', f"FAIL: {result1}"

# ── Step 2: assign_udim_materials ──
print("\n>>> Step 2: assign_udim_materials")
from assign_udim_materials import execute as assign_execute
result2 = assign_execute({
    'asset_name': 'hamaguai', 'project': 'ysj',
    'parameters': {
        'texmap_path': TEXMAP_PATH,
        'srcimg_root': SRCIMG_ROOT,
        'category': 'chr', 'stage': 'tex',
    }
})
print(json.dumps(result2, indent=2, ensure_ascii=False, default=str))
assert result2['status'] == 'SUCCESS', f"FAIL: {result2}"

# ── Step 3: save_scene ──
print("\n>>> Step 3: save")
os.makedirs(os.path.dirname(SAVE_PATH), exist_ok=True)
cmds.file(rename=SAVE_PATH)
cmds.file(save=True, type='mayaAscii')
print(f"Saved: {SAVE_PATH}")

# ── 验证 ──
print("\n>>> Verification")
meshes = cmds.ls(type='mesh', noIntermediate=True) or []
mats = [m for m in (cmds.ls(type='lambert') or []) if m != 'lambert1']
files = cmds.ls(type='file') or []
sgs = [s for s in (cmds.ls(type='shadingEngine') or []) if s not in ('initialShadingGroup', 'initialParticleSE')]
print(f"  Meshes: {len(meshes)}")
print(f"  Materials: {len(mats)}")
print(f"  File nodes: {len(files)}")
print(f"  Shading groups: {len(sgs)}")
print(f"  MA size: {os.path.getsize(SAVE_PATH)/1024/1024:.1f} MB")
print("\n=== TEST PASSED ===" if result1['status'] == 'SUCCESS' and result2['status'] == 'SUCCESS' else "\n=== TEST FAILED ===")

maya.standalone.uninitialize()
