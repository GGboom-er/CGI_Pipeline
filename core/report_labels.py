"""报告标签配置读取。"""

from __future__ import annotations

import json
from pathlib import Path

from core.bootstrap import cfg as _cfg


PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)
CONFIG_PATH = PROJECT_ROOT / "config" / "report_labels.json"

_DEFAULTS = {
    "cruise_report": {
        "metrics": {
            "paired": "完成配对",
            "identical": "通过配对",
            "matched_different": "几何差异",
            "only_source": "源侧独有",
            "only_target": "目标独有",
            "blocking": "阻断差异",
            "MODIFIED": "修改差异",
            "MERGE": "合并关系",
            "SPLIT": "拆分关系",
        },
        "sync_actions": {
            "IDENTICAL": "原样搬运",
            "ORIG_INJECT": "坐标注入",
            "PAIRED": "配对重建",
            "UNPAIRED": "新增构建",
            "target_only": "绑定独有",
        },
    }
}


def load_report_labels() -> dict:
    if not CONFIG_PATH.exists():
        return _DEFAULTS
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _DEFAULTS
    if not isinstance(data, dict):
        return _DEFAULTS

    merged = json.loads(json.dumps(_DEFAULTS, ensure_ascii=False))
    for section, section_data in data.items():
        if not isinstance(section_data, dict):
            continue
        merged.setdefault(section, {})
        for group, labels in section_data.items():
            if isinstance(labels, dict):
                merged[section].setdefault(group, {})
                merged[section][group].update(labels)
    return merged


def label(group: str, key: str, section: str = "cruise_report") -> str:
    labels = load_report_labels()
    return labels.get(section, {}).get(group, {}).get(key, key)
