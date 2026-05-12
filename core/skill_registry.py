# core/skill_registry.py
# 统一技能注册表加载 — 消除 tasks.py / dcc_factory.py / workflow_engine.py 三处重复

import json
import yaml
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent.parent / 'skills'
_skills: list[dict] = []
_skill_map: dict[str, dict] = {}
_skill_dcc_map: dict[str, str] = {}
_skip_audit: set[str] = set()


def _load():
    global _skills, _skill_map, _skill_dcc_map, _skip_audit
    
    loaded_skills = []
    
    if _SKILLS_DIR.exists():
        for skill_md in _SKILLS_DIR.glob('*/SKILL.md'):
            try:
                content = skill_md.read_text(encoding='utf-8')
                if content.startswith('---'):
                    end_idx = content.find('---', 3)
                    if end_idx != -1:
                        yaml_text = content[3:end_idx].strip()
                        skill_data = yaml.safe_load(yaml_text)
                        if skill_data and 'skill_id' in skill_data:
                            loaded_skills.append(skill_data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to load {skill_md}: {e}")

    _skills = loaded_skills
    _skill_map = {s['skill_id']: s for s in _skills}
    _skill_dcc_map = {s['skill_id']: s.get('dcc', 'maya') for s in _skills}
    _skip_audit = {s['skill_id'] for s in _skills if s.get('skip_audit', False)}


_load()


def reload():
    """热重载技能注册表"""
    global _skills, _skill_map, _skill_dcc_map, _skip_audit
    _skills = []
    _skill_map = {}
    _skill_dcc_map = {}
    _skip_audit = set()
    _load()


def get_all_skills() -> list[dict]:
    return _skills


def get_skill_map() -> dict[str, dict]:
    return _skill_map


def get_skill_dcc(skill_id: str) -> str:
    return _skill_dcc_map.get(skill_id, 'maya')


def get_skip_audit_skills() -> set[str]:
    return _skip_audit
