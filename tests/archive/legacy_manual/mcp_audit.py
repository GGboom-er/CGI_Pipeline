# tests/mcp_audit.py — MCP 完整流程审计
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.config_loader import load_project_config
from core.asset_resolver import AssetResolver

cfg = load_project_config('ysj')
resolver = AssetResolver(cfg)

print('=== 1. 配置信息 ===')
print(f'server_root: {cfg["server_root"]}')
print(f'ai_publish_root: {cfg["ai_publish_root"]}')
print(f'categories: {list(cfg["categories"].keys())}')
print(f'stages: {list(cfg["stages"].keys())}')
print()

print('=== 2. resolve_for_mcp (xtbyao) ===')
for stage in ['tex', 'rig']:
    r = resolver.resolve_for_mcp(category='chr', asset_name='xtbyao', stage=stage)
    print(f'xtbyao/{stage}:')
    print(f'  latest_publish: {r.get("latest_publish", "N/A")}')
    print(f'  latest_exists:  {r.get("latest_exists", False)}')
    print(f'  info_json:      {r.get("info_json", "N/A")}')
    print(f'  versions:       {r.get("total_versions", 0)}')
    print()

print('=== 3. compare_asset (tex_json vs rig_json) ===')
from skills.compare_asset import execute as compare_execute

tex_json = r'y:\runs\assets\chr\xtbyao\tex\texMaster\.info\ysj_chr_xtbyao_tex_texMaster_v002.json'
rig_json = r'y:\runs\assets\chr\xtbyao\rig\rigMaster\.info\ysj_chr_xtbyao_rig_rigMaster_v002.json'

receipt = compare_execute({
    'parameters': {'input_a': tex_json, 'input_b': rig_json}
})
print(f'status: {receipt["status"]}')
o = receipt['outputs']
rp = o.get('report_path', '')
print(f'report:   {os.path.abspath(rp) if rp else "N/A"}')
print(f'paired:   {o.get("paired",0)} (identical={o.get("identical",0)}, auto_safe={o.get("auto_safe",0)}, review={o.get("review",0)})')
print(f'only_a:   {o.get("only_a",0)}, only_b: {o.get("only_b",0)}')
print(f'report exists: {os.path.isfile(rp)}')
print()

print('=== 4. 报告内容校验 ===')
if os.path.isfile(rp):
    with open(rp, 'r', encoding='utf-8') as f:
        content = f.read()
    for name, ok in [
        ('标题含 FAIL',  'FAIL' in content),
        ('含对比来源',    '对比来源' in content),
        ('含 tex_json',  'tex_json' in content),
        ('含 rig_json',  'rig_json' in content),
        ('含源文件',      '源文件' in content),
        ('含 JSON:',     'JSON:' in content),
        ('含完全一致',    '完全一致' in content),
        ('含需人工确认',  '需人工确认' in content),
    ]:
        print(f'  {"PASS" if ok else "FAIL"}  {name}')
print()

print('=== 5. registry.json 技能清单 ===')
from core.skill_registry import get_all_skills
skills = get_all_skills()
for s in skills:
    print(f'  [{s["dcc"]:8s}] {s["skill_id"]:25s} - {s["name"]}')
print(f'  总计: {len(skills)}')
print()

print('=== 6. tex_json vs tex_json (同源自比) ===')
from core.asset_info_schema import compare
with open(tex_json, 'r', encoding='utf-8') as f:
    tex_info = json.load(f)
r2 = compare(tex_info, tex_info, label_a='tex_v1', label_b='tex_v2')
n_mesh = len(tex_info['meshes'])
n_ident = sum(1 for p in r2['paired'] if p.get('actionability') == 'IDENTICAL')
print(f'  meshes: {n_mesh}, paired: {len(r2["paired"])}, identical: {n_ident}')
print(f'  only_a: {len(r2["only_a"])}, only_b: {len(r2["only_b"])}')
print(f'  PASS: {n_ident == n_mesh and len(r2["only_a"]) == 0}')
