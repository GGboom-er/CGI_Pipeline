#!/usr/bin/env python3
"""Verify CGI Pipeline through Maya's real module and Python runtime."""

from __future__ import annotations

import json
import sys
from typing import Any


def _smoke_skin_query(cmds: Any, api: Any) -> dict[str, Any]:
    cmds.file(new=True, force=True)
    mesh = cmds.polyPlane(name="cgiDoctorMesh", subdivisionsX=1, subdivisionsY=1)[0]
    cmds.select(clear=True)
    joint_a = cmds.joint(name="cgiDoctorJointA", position=(0, 0, 0))
    cmds.select(clear=True)
    joint_b = cmds.joint(name="cgiDoctorJointB", position=(1, 0, 0))
    skin = cmds.skinCluster([joint_a, joint_b], mesh, toSelectedBones=True)[0]
    cmds.skinPercent(
        skin,
        f"{mesh}.vtx[0]",
        transformValue=[(joint_a, 0.75), (joint_b, 0.25)],
    )
    return api.execute_local(
        "maya.rig.skin.query_weights",
        {
            "mesh": mesh,
            "vertices": [0],
            "influence": "",
            "minimum_weight": 0.0,
            "max_vertices": 1,
        },
        host="maya",
        host_modules={"cmds": cmds},
    )


def run() -> dict[str, Any]:
    import maya.cmds as cmds

    service_modules = {"celery", "redis", "fastmcp", "mcp"}
    for name in ("cgi_pipeline", *service_modules):
        sys.modules.pop(name, None)

    import cgi_pipeline

    initial_service_imports = sorted(service_modules & set(sys.modules))
    from cgi_pipeline import api, tools

    module_names = cmds.moduleInfo(listModules=True) or []
    module_path = ""
    if "CGIPipeline" in module_names:
        module_path = cmds.moduleInfo(moduleName="CGIPipeline", path=True) or ""
    capability_ids = {
        row["api_id"]
        for row in api.list(executor="maya", stages=["rigging"], access="read")
    }
    packages = tools.list(host="maya")
    adpose_entrypoint = tools._load_entrypoint("adpose", "maya")
    receipt = _smoke_skin_query(cmds, api)

    checks = {
        "module_discovered": "CGIPipeline" in module_names,
        "module_path": bool(module_path),
        "public_namespace": cgi_pipeline.__all__ == ["api", "client", "tools"],
        "host_safe_import": not initial_service_imports,
        "skin_capability": "maya.rig.skin.query_weights" in capability_ids,
        "skin_query": receipt.get("status") == "SUCCESS",
        "adpose_registered": any(row["package_id"] == "adpose" for row in packages),
        "adpose_import": callable(adpose_entrypoint),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "maya_version": cmds.about(version=True),
        "python_version": sys.version.split()[0],
        "qt_version": __import__("PySide6").__version__,
        "module_path": module_path,
        "checks": checks,
        "receipt": receipt,
    }


def main() -> int:
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        result = run()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    raise SystemExit(main())

