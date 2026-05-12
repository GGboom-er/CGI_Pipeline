import sys
import os
import json

pipeline_path = r"y:\GGbommer\scripts\CGI_Pipeline"
if pipeline_path not in sys.path:
    sys.path.insert(0, pipeline_path)

from skills.compare_asset import execute

payload = {
    "parameters": {
        "input_a": r"y:\runs\assets\chr\xtbyao\tex\texMaster\ysj_chr_xtbyao_tex_texMaster_v002.abc",
        "input_b": r"y:\runs\assets\chr\xtbyao\tex\texMaster\.info\ysj_chr_xtbyao_tex_texMaster_v002.json"
    }
}

res = execute(payload)

print("=== COMPARE RESULT ===")
print(json.dumps(res, indent=2, ensure_ascii=False))
print("======================")
