import os
import sys
from pathlib import Path
from pydantic import ValidationError

PROJECT_ROOT = r'y:\GGbommer\scripts\CGI_Pipeline'
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.schemas import SkillPayloadSchema

def _translate_error(detail: str, skill_id: str) -> str:
    if "No such file or directory" in detail or "FileNotFoundError" in detail or "文件不存在或为空" in detail:
        return f"[文件读取异常] 尝试读取的源文件不存在或被占用，请检查上游环节是否已正确发布。原生报错: {detail[:200]}"
    if "IndexError" in detail and "list index out of range" in detail:
        return f"[索引越界] {skill_id} 技能执行时遇到数组越界，这通常是因为场景中缺失预期的节点或组件（如空组、空层级）。原生报错: {detail[:200]}"
    if "KeyError" in detail:
        return f"[数据缺失] 缺少关键数据键值。原生报错: {detail[:200]}"
    if "RuntimeError" in detail and "Object does not exist" in detail:
        return f"[节点丢失] Maya/Blender 场景中找不到指定的节点，可能是因为名称被篡改或已被删除。原生报错: {detail[:200]}"
    return detail

def test_validation():
    print("--- 1. Testing Validation (Fail-Fast) ---")
    valid_payload = {
        "task_id": "test_001",
        "skill_id": "maya_clean_skinweights",
        "project": "ysj",
        "asset_name": "test_asset",
        "parameters": {
            "threshold": 0.01
        }
    }
    
    try:
        # This should pass and fill default missing params
        parsed = SkillPayloadSchema(**valid_payload)
        print("Valid Payload OK:", parsed.parameters)
    except ValidationError as e:
        print("Error on Valid Payload:", e)

    invalid_payload = {
        "task_id": "test_002",
        "skill_id": "maya_clean_skinweights",
        "project": "ysj",
        "asset_name": "test_asset",
        "parameters": {
            "threshold": 0.01,
            "dirty_extra_param": "some_value" # Should be rejected
        }
    }

    try:
        parsed = SkillPayloadSchema(**invalid_payload)
        print("Invalid Payload unexpectedly passed:", parsed)
    except Exception as e:
        print("Invalid Payload correctly rejected:")
        print(e)


def test_dehydration():
    print("\n--- 2. Testing Error Dehydration ---")
    
    err_file = "FileNotFoundError: [Errno 2] No such file or directory: 'X:/some/path'"
    trans_file = _translate_error(err_file, "open_file")
    print(f"File Error:\nOriginal: {err_file}\nTranslated: {trans_file}\n")
    
    err_index = "IndexError: list index out of range"
    trans_index = _translate_error(err_index, "sync_rig_asset")
    print(f"Index Error:\nOriginal: {err_index}\nTranslated: {trans_index}\n")

    err_runtime = "RuntimeError: Object does not exist"
    trans_runtime = _translate_error(err_runtime, "fix_shape_names")
    print(f"Runtime Error:\nOriginal: {err_runtime}\nTranslated: {trans_runtime}\n")

if __name__ == '__main__':
    test_validation()
    test_dehydration()
