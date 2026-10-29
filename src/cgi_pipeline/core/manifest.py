# cgi_pipeline/core/manifest.py
# 系统宪法加载器 — 所有模块通过此获取系统级配置
#
# 金字塔配置层级：
#   Layer 0: pipeline_manifest.json（此加载器）
#   Layer 1: {project}_config.json（项目配置）
#   Layer 2: capabilities/**/capability.yaml（能力契约与帮助）
#   Layer 3: .env（环境变量）

import json
import re

from cgi_pipeline.paths import CONFIG_ROOT

_MANIFEST_PATH = CONFIG_ROOT / 'pipeline_manifest.json'
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


# Win32 设备/长路径前缀（分隔符已统一为 '/'、已小写）：'\\?\'、'\\.\'、'\??\'，可带 'UNC\'。
_DEVICE_PREFIX = re.compile(r'^(?://[?.]/|/\?\?/)(unc/)?')


def normalize_path(path: str) -> str:
    r"""受保护根的显示与 path_guard 拦截共用的唯一规范化。

    剥掉设备前缀（'\\?\X:\a' -> 'x:/a'，'\\?\UNC\server\s' -> '//server/s'），
    统一为小写正斜杠、合并重复分隔符（保留 UNC 开头的 '//'）、去掉结尾 '/'。
    """
    value = str(path).replace('\\', '/').lower()
    prefix = _DEVICE_PREFIX.match(value)
    if prefix:
        value = ('//' if prefix.group(1) else '') + value[prefix.end():]
    collapsed = re.sub(r'/+', '/', value)
    if value.startswith('//'):
        collapsed = '/' + collapsed
    return collapsed.rstrip('/')


def _canonical_drive(entry: str) -> str:
    """'x'、'X:'、'x:/'、'x:\\' 统一为 path_guard 比较用的 'x:'。"""
    return normalize_path(entry).rstrip(':') + ':'


def get_readonly_drives() -> frozenset:
    m = get_manifest()
    return frozenset(_canonical_drive(d) for d in m.get('principles', {}).get('readonly_drives', []))


def get_readonly_unc_prefixes() -> list[str]:
    m = get_manifest()
    prefixes = (normalize_path(p) for p in m.get('principles', {}).get('readonly_unc_prefixes', []))
    # '//server/' 与 '\\server\' 是同一根，去重后保持登记顺序。
    return list(dict.fromkeys(prefixes))


def get_status_codes() -> dict:
    return get_manifest().get('status_codes', {})


def is_valid_status(status: str) -> bool:
    return status in get_status_codes()


def get_executor_queue(executor: str) -> str:
    return get_manifest().get('executor_queues', {}).get(executor, 'cgi_queue')


def get_executor_queue_map() -> dict:
    return dict(get_manifest().get('executor_queues', {}))


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
