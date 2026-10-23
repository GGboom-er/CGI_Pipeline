"""Validate the pinned Maya MCP research and migration sources."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_ROOT = ROOT / "integrations" / "maya" / "upstreams"
REGISTRY = UPSTREAM_ROOT / "registry.yaml"
INVENTORY = UPSTREAM_ROOT / "candidate_inventory.json"
RECONCILIATION = UPSTREAM_ROOT / "reconciliation.json"


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data).hexdigest()


def _repositories() -> list[dict]:
    payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["snapshot_policy"]["commit_full_repositories"] is False
    return payload["repositories"]


def test_registry_pins_eight_unique_sources_to_full_commits():
    rows = _repositories()
    assert len(rows) == 8
    assert len({row["source_id"] for row in rows}) == 8
    assert len({row["remote_url"] for row in rows}) == 8
    assert all(re.fullmatch(r"[0-9a-f]{40}", row["commit"]) for row in rows)
    assert {row["disposition"] for row in rows} <= {
        "migrate",
        "reimplement",
        "reference",
    }
    assert all(row["uses"] for row in rows)


def test_verified_license_copies_match_pinned_git_blobs():
    rows = _repositories()
    verified = [row for row in rows if row["license"]["status"] == "verified_root_file"]
    missing = [row for row in rows if row["license"]["status"] == "missing_root_file"]
    assert len(verified) == 5
    assert len(missing) == 3

    for row in verified:
        license_info = row["license"]
        path = UPSTREAM_ROOT / license_info["local_copy"]
        assert path.is_file()
        assert license_info["spdx"] == "MIT"
        assert _git_blob_sha(path) == license_info["upstream_blob_sha"]

        notice_path = license_info.get("notice_local_copy")
        if notice_path:
            notice = UPSTREAM_ROOT / notice_path
            assert notice.is_file()
            assert _git_blob_sha(notice) == license_info["notice_upstream_blob_sha"]

    for row in missing:
        license_info = row["license"]
        assert row["disposition"] == "reference"
        assert license_info["spdx"] == "NOASSERTION"
        assert license_info["local_copy"] is None


def test_no_full_upstream_repository_is_committed():
    forbidden = [path for path in UPSTREAM_ROOT.rglob(".git")]
    assert forbidden == []
    snapshot_root = UPSTREAM_ROOT / "snapshots"
    assert not snapshot_root.exists() or not any(snapshot_root.iterdir())


def test_candidate_inventory_has_stable_extracted_entry_counts():
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["candidate_count"] == 387
    assert len(payload["candidates"]) == 387
    assert len({row["candidate_id"] for row in payload["candidates"]}) == 387
    assert payload["summary"]["by_source"] == {
        "dcc_mcp_maya": 229,
        "gg_mayamcp": 71,
        "chadrik_maya_mcp_server": 4,
        "patrickpalmer_mayamcp": 18,
        "damaya_mcp": 6,
        "maya_mcp_server_enhanced": 18,
        "abrahamadsk_maya_mcp": 16,
        "aydji_autodesk_maya_mcp": 25,
    }


def test_reconciliation_classifies_every_candidate_without_tool_sprawl():
    payload = json.loads(RECONCILIATION.read_text(encoding="utf-8"))
    assert payload["candidate_count"] == 387
    assert payload["summary"]["candidate_decisions"] == {
        "accept": 207,
        "merge": 143,
        "defer": 37,
    }
    assert payload["summary"]["semantic_clusters"] == {
        "accept": 58,
        "defer": 37,
        "merge": 33,
    }
    assert payload["summary"]["accepted_priorities"] == {
        "P1": 19,
        "P2": 27,
        "P3": 12,
    }
    assert payload["summary"]["accepted_implementation"] == {
        "implemented": 22,
        "not_reconciled": 36,
    }
    assert payload["summary"]["accepted_verification"] == {
        "contract_tested": 20,
        "not_reconciled": 36,
        "live_tested": 2,
    }
    assert "unresolved" not in payload["summary"]["candidate_decisions"]

    accepted = {
        row["canonical_api_id"]
        for row in payload["clusters"]
        if row["decision"] == "accept"
    }
    assert {
        "maya.animation.keyframes.manage",
        "maya.animation.time.manage",
        "maya.rig.blendshape.manage",
        "maya.rig.constraints.manage",
        "maya.rig.skin.set_weights",
    } <= accepted

    migrated = {
        row["canonical_api_id"]: (row["implementation_status"], row["verification_status"])
        for row in payload["clusters"]
        if row["decision"] == "accept" and row["implementation_status"] == "implemented"
    }
    assert migrated == {
        "maya.animation.bake": ("implemented", "contract_tested"),
        "maya.animation.curves.manage": ("implemented", "contract_tested"),
        "maya.animation.expressions.manage": ("implemented", "contract_tested"),
        "maya.animation.keyframes.manage": ("implemented", "contract_tested"),
        "maya.animation.poses.manage": ("implemented", "contract_tested"),
        "maya.animation.time.manage": ("implemented", "contract_tested"),
        "maya.rig.blendshape.manage": ("implemented", "contract_tested"),
        "maya.rig.constraints.manage": ("implemented", "contract_tested"),
        "maya.rig.controls.create": ("implemented", "contract_tested"),
        "maya.rig.deformers.manage": ("implemented", "contract_tested"),
        "maya.rig.driven_keys.manage": ("implemented", "contract_tested"),
        "maya.rig.framework.inspect": ("implemented", "live_tested"),
        "maya.rig.ik.manage": ("implemented", "contract_tested"),
        "maya.rig.joints.manage": ("implemented", "contract_tested"),
        "maya.rig.skin.bind": ("implemented", "contract_tested"),
        "maya.rig.skin.copy_weights": ("implemented", "contract_tested"),
        "maya.rig.skin.set_weights": ("implemented", "contract_tested"),
        "maya.scene.attributes.schema": ("implemented", "contract_tested"),
        "maya.scene.components.select": ("implemented", "contract_tested"),
        "maya.scene.measurements.inspect": ("implemented", "live_tested"),
        "maya.scene.nodes.resolve": ("implemented", "contract_tested"),
        "maya.scene.nodes.state": ("implemented", "contract_tested"),
    }

    deferred = {
        row["name"]: row["reason"]
        for row in payload["candidates"]
        if row["candidate_id"] in {"MAYA-004", "MAYA-009"}
    }
    assert deferred == {
        "export_animation_curves": "requires_sandboxed_curve_transfer_contract",
        "import_animation_curves": "requires_sandboxed_curve_transfer_contract",
    }


def test_attribute_discovery_is_not_mapped_to_known_plug_value_queries():
    from tools.reconcile_maya_upstreams import reconcile

    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    catalog_path = ROOT / "src/cgi_pipeline/data/capabilities.json"
    result = reconcile(inventory, json.loads(catalog_path.read_text(encoding="utf-8")))
    rows = [row for row in result["candidates"] if row["name"] == "list_attributes"]
    assert rows
    assert all(row["canonical_api_id"] == "maya.scene.attributes.inspect" for row in rows)
    assert all(row["implementation_status"] == "implemented" for row in rows)
