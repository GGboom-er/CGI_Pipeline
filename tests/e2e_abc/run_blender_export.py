# tests/e2e_abc/run_blender_export.py
# 用 Blender headless 调用 blender_export_abc 技能导出 ABC
import sys, os, json

# 把技能目录加入 path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'skills'))

from core.config_loader import load_project_config
from blender_export_abc import execute

cfg = load_project_config('ysj')
tex_group = cfg.get('stages', {}).get('tex', {}).get('geom_roots', ['cache'])[0]

output_dir = os.path.join(os.path.dirname(__file__), 'output').replace('\\', '/')
os.makedirs(output_dir, exist_ok=True)
abc_path = output_dir + '/hamaguai_test.abc'

result = execute({
    'source_path': '',
    'asset_name': 'hamaguai',
    'project': 'ysj',
    'parameters': {
        'abc_path': abc_path,
        'cache_group': tex_group,
    }
})

print("=" * 60)
print("BLENDER EXPORT RESULT:")
print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
print("=" * 60)

# 写结果到文件给后续 Maya 步骤读取
with open(os.path.join(output_dir, 'blender_result.json'), 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
