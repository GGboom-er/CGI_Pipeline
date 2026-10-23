"""Build and verify the dedicated automatic scene initialization operation."""
from __future__ import annotations

import json
import os


_NEW_SCENE = '<new_scene>'
_NON_NATIVE = {'.abc', '.fbx', '.obj', '.usd', '.usda', '.usdc'}


def _scene_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def opens_empty_scene(source_path: str) -> bool:
    return not source_path or os.path.splitext(source_path)[1].lower() in _NON_NATIVE


def build_open_code(source_path: str, dcc_type: str) -> str:
    """Explicit open always reads disk; only scene_session may reuse memory."""
    empty = opens_empty_scene(source_path)
    lines = ['import os, time', f'sp = {source_path!r}']
    if not empty:
        lines.extend([
            'for _ in range(20):',
            '    try:',
            '        if os.path.isfile(sp) and os.path.getsize(sp) > 0:',
            '            break',
            '    except OSError:',
            '        pass',
            '    time.sleep(0.5)',
            'else:',
            '    raise FileNotFoundError(f"Source file missing or empty after staging: {sp}")',
        ])
    if dcc_type == 'blender':
        lines.append('import bpy')
        lines.append('opened = bpy.ops.wm.read_factory_settings(use_empty=True)' if empty else
                     'opened = bpy.ops.wm.open_mainfile(filepath=sp, load_ui=False)')
        lines.extend([
            'if not opened or "FINISHED" not in opened:',
            '    raise RuntimeError(f"Blender scene open did not finish: {opened}")',
            'actual = bpy.data.filepath or ""',
        ])
    else:
        lines.extend([
            'import maya.cmds as cmds',
            'cmds.file(new=True, force=True)' if empty else
            'cmds.file(sp, open=True, force=True)',
            'actual = cmds.file(q=True, sn=True) or ""',
        ])
    lines.extend([
        'expected = ""' if empty else 'expected = sp',
        'if (actual and not expected) or (expected and (not actual or',
        '        os.path.normcase(os.path.abspath(actual)) != os.path.normcase(os.path.abspath(expected)))):',
        '    raise RuntimeError(f"Opened scene mismatch: expected {expected!r}, got {actual!r}")',
        f'result = {{"operation": "open_source_file", "dcc": {dcc_type!r},',
        f'          "status": "SUCCESS", "scene": {_NEW_SCENE!r}}}' if empty else
        '          "status": "SUCCESS", "scene": actual}',
    ])
    return '\n'.join(lines) + '\n'


def confirm_open_result(response, source_path: str, dcc_type: str) -> tuple[bool, str]:
    """Check the exec receipt and its dedicated result, never arbitrary scripts."""
    if not isinstance(response, dict):
        return False, 'Scene open transport returned a non-object result'
    if response.get('status') != 'SUCCESS':
        return False, str(response.get('detail') or 'Scene open transport failed')
    receipt = response.get('detail')
    if isinstance(receipt, str):
        try:
            receipt = json.loads(receipt)
        except (TypeError, ValueError):
            return False, 'Scene open transport returned invalid receipt JSON'
    if not isinstance(receipt, dict):
        return False, 'Scene open receipt must be an object'
    if receipt.get('status') != 'SUCCESS':
        return False, str(receipt.get('error') or 'Scene open execution failed')
    output = receipt.get('output')
    result = output.get('result') if isinstance(output, dict) else None
    if (not isinstance(result, dict) or result.get('operation') != 'open_source_file'
            or result.get('dcc') != dcc_type or result.get('status') != 'SUCCESS'):
        return False, 'Scene open receipt is missing its confirmed operation result'
    actual = result.get('scene')
    if opens_empty_scene(source_path):
        matches = actual == _NEW_SCENE
    else:
        matches = (isinstance(actual, str) and bool(actual)
                   and _scene_key(actual) == _scene_key(source_path))
    if not matches:
        return False, f'Scene open result path mismatch: expected {source_path!r}, got {actual!r}'
    return True, ''
