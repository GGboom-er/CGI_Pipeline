#!/usr/bin/env python3
"""Audit CGI Pipeline SKILL.md routing metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
ALLOWED_TIERS = {"read", "write", "destructive"}


def load_skills(skills_dir: Path = SKILLS_DIR) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            skills.append({"path": str(skill_md), "error": "missing frontmatter"})
            continue
        end = text.find("---", 3)
        if end == -1:
            skills.append({"path": str(skill_md), "error": "unterminated frontmatter"})
            continue
        data = yaml.safe_load(text[3:end].strip()) or {}
        data["path"] = str(skill_md)
        skills.append(data)
    return skills


def audit(skills: list[dict[str, Any]]) -> dict[str, Any]:
    ids = {s.get("skill_id") for s in skills if s.get("skill_id")}
    issues: list[str] = []
    tiers = {tier: 0 for tier in sorted(ALLOWED_TIERS)}
    missing_tier: list[str] = []
    missing_pairs: list[str] = []

    for skill in skills:
        skill_id = skill.get("skill_id") or skill.get("path", "<unknown>")
        if skill.get("error"):
            issues.append(f"{skill_id}: {skill['error']}")
            continue

        tier = skill.get("tier")
        if tier not in ALLOWED_TIERS:
            missing_tier.append(skill_id)
            issues.append(f"{skill_id}: invalid tier {tier!r}")
        else:
            tiers[tier] += 1

        pairs = skill.get("pairs_with", None)
        if pairs is None:
            missing_pairs.append(skill_id)
            issues.append(f"{skill_id}: missing pairs_with")
        elif not isinstance(pairs, list):
            issues.append(f"{skill_id}: pairs_with must be a list")
        else:
            for pair in pairs:
                if pair not in ids:
                    issues.append(f"{skill_id}: pairs_with unknown skill_id {pair!r}")

    return {
        "total": len(skills),
        "tiers": tiers,
        "missing_tier": missing_tier,
        "missing_pairs_with": missing_pairs,
        "issues": issues,
        "ok": not issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit tier and pairs_with in SKILL.md files.")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    result = audit(load_skills())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"skills: {result['total']}")
        print(
            "tiers: "
            + " / ".join(f"{tier}={count}" for tier, count in result["tiers"].items())
        )
        print(f"issues: {len(result['issues'])}")
        for issue in result["issues"]:
            print(f"- {issue}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
