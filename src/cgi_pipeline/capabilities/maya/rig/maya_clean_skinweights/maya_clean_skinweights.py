"""Registered receipt boundary for explicit, reusable skin-weight cleanup."""

import os
import time

import maya.cmds as cmds

from cgi_pipeline.core.receipt import make_receipt


def execute(payload: dict) -> dict:
    started = time.time()
    params = dict(payload.get("parameters") or {})
    scene_name = os.path.basename(cmds.file(query=True, sceneName=True) or "untitled")
    if not params.get("skin_clusters") and not (cmds.ls(type="skinCluster") or []):
        return make_receipt("maya_clean_skinweights", "SUCCESS", started,
                            output={"skins": [], "changed": False},
                            summary_input=scene_name,
                            summary_action="Empty scene; no skin cleanup needed")
    try:
        method = params.get("method", "dair_inspired")
        if method == "dair_inspired":
            from .dair_engine import run
            if not params.get("solver_workdir") and payload.get("run_dir"):
                params["solver_workdir"] = os.path.join(payload["run_dir"], "work", "skin_solver")
        elif method == "ng_proportional":
            from .ng_engine import run
        else:
            raise ValueError("method must be dair_inspired or ng_proportional")

        output = run(params)
        return make_receipt("maya_clean_skinweights", "SUCCESS", started,
                            input=params, output=output, summary_input=scene_name,
                            summary_action=method + " skin cleanup",
                            summary_count=len(output["skins"]), summary_label="skinCluster")
    except ValueError as exc:
        return make_receipt("maya_clean_skinweights", "BLOCKED", started,
                            input=params, summary_input=scene_name,
                            error=str(exc), error_code="SKIN_CLEANUP_PRECONDITION",
                            recovery_hint="Inspect explicit targets and the selected engine's preconditions; no automatic retry.")
    except ImportError as exc:
        return make_receipt("maya_clean_skinweights", "NEEDS_ATTENTION", started,
                            input=params, error=str(exc),
                            error_code="SKIN_CLEANUP_ENGINE_REQUIRED",
                            recovery_hint="DAIR-inspired requires Maya NumPy and the registered external numeric runtime; ng_proportional requires loaded ngSkinTools2 2.4.0.")
    except RuntimeError as exc:
        return make_receipt("maya_clean_skinweights", "ERROR", started,
                            input=params, error=str(exc), error_code="SKIN_CLEANUP_FAILED",
                            recovery_hint="Read the rollback result before any retry; recover the saved sandbox if verification failed.")
