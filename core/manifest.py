# core/manifest.py
# 系统宪法加载器 — 所有模块通过此获取系统级配置
#
# 金字塔配置层级：
#   Layer 0: pipeline_manifest.json（此加载器）
#   Layer 1: {project}_config.json（项目配置）
#   Layer 2: skills/*/SKILL.md → core/skill_registry.py（动态技能注册）
#   Layer 3: .env（环境变量）

import json
from pathlib import Path

_MANIFEST_PATH = Path(__file__).parent.parent / 'config' / 'pipeline_manifest.json'
_manifest: dict | None = None


def _load():
    global _manifest
    if not _MANIFEST_PATH.exists():
        raise FileNotFoundError(f'系统宪法文件不存在: {_MANIFEST_PATH}')
    _manifest = json.loads(_MANIFEST_PATH.read_text(encoding='utf-8'))


def reload():
    """热重载宪法配置"""
    global _manifest
    _manifest = None
    _load()


def get_manifest() -> dict:
    if _manifest is None:
        _load()
    return _manifest


def get_readonly_drives() -> frozenset:
    m = get_manifest()
    return frozenset(d.lower() for d in m.get('principles', {}).get('readonly_drives', []))


def get_readonly_unc_prefixes() -> list[str]:
    m = get_manifest()
    return [p.replace('\\', '/').rstrip('/').lower()
            for p in m.get('principles', {}).get('readonly_unc_prefixes', [])]


def get_status_codes() -> dict:
    return get_manifest().get('status_codes', {})


def is_valid_status(status: str) -> bool:
    return status in get_status_codes()


def get_dcc_queue(dcc: str) -> str:
    return get_manifest().get('dcc_queues', {}).get(dcc, 'dcc_queue')


def get_dcc_queue_map() -> dict:
    return dict(get_manifest().get('dcc_queues', {}))


def get_chain_rules() -> dict:
    return get_manifest().get('chain_rules', {})


def get_exec_code_safety_rules() -> list[str]:
    return get_manifest().get('exec_code_safety', [])


def get_payload_schema() -> dict:
    return get_manifest().get('payload_schema', {})


def validate_payload(payload: dict) -> tuple[bool, list[str]]:
    schema = get_payload_schema()
    errors = []
    for field in schema.get('required', []):
        if field not in payload:
            errors.append(f'缺少必须字段: {field}')
    return len(errors) == 0, errors
