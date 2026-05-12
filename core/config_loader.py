# core/config_loader.py
# ── 统一配置加载入口 ──
#
# 金字塔配置层级：
#   Layer 0: pipeline_manifest.json → core/manifest.py
#   Layer 1: {project}_config.json → 此文件
#   Layer 2: skills/*/SKILL.md → core/skill_registry.py
#   Layer 3: .env → 环境变量

import json
import os
from pathlib import Path

from core.bootstrap import cfg as _cfg

_PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)
_CONFIG_DIR = _PROJECT_ROOT / 'config'


def get_config_path(project: str) -> Path:
    return _CONFIG_DIR / f'{project}_config.json'


_cache: dict[str, dict] = {}


def load_project_config(project: str) -> dict:
    cfg_path = get_config_path(project)
    cache_key = str(cfg_path)
    if cache_key in _cache:
        return _cache[cache_key]
    if not cfg_path.exists():
        raise FileNotFoundError(f'项目配置不存在: {cfg_path}')
    data = json.loads(cfg_path.read_text(encoding='utf-8'))
    _cache[cache_key] = data
    return data


def reload_config(project: str) -> dict:
    cfg_path = get_config_path(project)
    cache_key = str(cfg_path)
    _cache.pop(cache_key, None)
    return load_project_config(project)


def get_server_root(project: str) -> str:
    return load_project_config(project).get('server_root', '')


def get_path_roots(project: str) -> dict:
    return load_project_config(project).get('path_roots', {})


def get_path_root(project: str, root_key: str) -> str:
    cfg = load_project_config(project)
    server = cfg.get('server_root', '')
    roots = cfg.get('path_roots', {})
    rel = roots.get(root_key, '')
    if not server or not rel:
        return ''
    return f'{server}/{rel}'


def get_source_root(project: str) -> str:
    """兼容旧接口：返回 server_root + path_roots["assets"]"""
    cfg = load_project_config(project)
    if 'server_root' in cfg:
        server = cfg['server_root']
        rel = cfg.get('path_roots', {}).get('assets', '')
        return f'{server}/{rel}' if rel else server
    return cfg.get('source_root', '')



def get_protected_roots(project: str) -> list[str]:
    return load_project_config(project).get('protected_roots', [])


# ═══════════════════════════════════════════
# Rig-Sync Profile
# ═══════════════════════════════════════════

_DEFAULT_RIG_SYNC_PROFILE = {
    'pairing': {
        'enable_cpd': True,
        'cpd_point_diff_threshold': 0.8,
        'cpd_max_iterations': 30,
        'cpd_tolerance': 0.001,
        'chamfer_threshold': 5.0,
        'bbox_iou_min': 0.01,
        'name_bonus': 0.0,
        'kdtree_round_decimals': 4,
        'auto_approve_threshold': 0.95,
        'review_threshold': 0.80,
        'strict_mode': False,
    },
    'classification': {
        'precision_exact': 0.0001,
        'precision_loose': 0.005,
        'reorder_require_100pct': True,
        'partial_match_min_pct': 0.99,
    },
    'tnb_projection': {
        'max_normal_angle_deg': 90.0,
        'k_candidates': 8,
        'bary_outgrow': 0.1,
    },
    'diffuse': {
        'backend': 'scipy',
        'fallback_sigma': 0.05,
        'fallback_k': 3,
    },
    'rig_prefix': 'RIG_',
    'min_dot': 0.0,
    'max_k_search': 50,
}


def _deep_merge(default: dict, override: dict) -> dict:
    """深拷贝 default 并用 override 覆盖。override 缺失的字段保留 default。"""
    import copy
    result = copy.deepcopy(default)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def get_default_rig_sync_profile() -> dict:
    """返回一份默认 profile 的深拷贝（供 compare() 等无项目上下文时使用）。"""
    import copy
    return copy.deepcopy(_DEFAULT_RIG_SYNC_PROFILE)


def get_rig_sync_profile(project: str) -> dict:
    """读取项目配置并与默认值深合并。项目未配置 rig_sync_profile 时返回默认值。"""
    try:
        cfg = load_project_config(project)
    except FileNotFoundError:
        return get_default_rig_sync_profile()
    user_profile = cfg.get('rig_sync_profile', {})
    return _deep_merge(_DEFAULT_RIG_SYNC_PROFILE, user_profile)


def get_full_context(project: str) -> dict:
    """返回合并后的完整配置（manifest + 项目配置），供需要全局视图的模块使用"""
    from core.manifest import get_manifest
    manifest = get_manifest()
    project_cfg = load_project_config(project)
    return {
        'manifest': manifest,
        'project': project_cfg,
        'principles': manifest.get('principles', {}),
        'status_codes': manifest.get('status_codes', {}),
        'chain_rules': manifest.get('chain_rules', {}),
    }
