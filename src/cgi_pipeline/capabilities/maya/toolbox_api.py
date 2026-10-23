"""Thin execution boundary over the registered Notes toolbox and retarget source."""
import importlib
import json
from pathlib import Path
import sys
import time

from cgi_pipeline.contracts import make_api_receipt


def _module(name):
    catalog = Path(__file__).resolve().parents[2] / "data/packages.json"
    package = next(row for row in json.loads(catalog.read_text(encoding="utf-8"))
                   if row["package_id"] == "pipeline_shelf")
    root = Path(package["source"]["path"]).resolve()
    if not (root / "DCC/maya/tool_catalog.py").is_file():
        raise FileNotFoundError("Registered toolbox source is unavailable")
    for relative in package["python_paths"]:
        path = str(root / relative)
        if path not in sys.path:
            sys.path.append(path)
    module = importlib.import_module(name)
    if not Path(module.__file__).resolve().is_relative_to(root / "DCC/maya"):
        raise RuntimeError("Tool module loaded from another source: " + name)
    return module


def execute(params, context):
    started = time.perf_counter()
    api_id = context.extras["_api_id"]
    try:
        if api_id == "maya.animation.retarget.transfer":
            output = _module("DCC.maya.anim_tools.retarget.scene").transfer(**params)
        elif api_id == "maya.rig.skeleton.normalize_scales":
            output = _module("DCC.maya.rig_tools.skeleton.zero_joint_scales").normalize(**params)
        else:
            raise ValueError("Unknown toolbox API: " + api_id)
        return make_api_receipt(api_id, "1.0.0", "SUCCESS", started, input_data=dict(params), output=output)
    except Exception as error:
        return make_api_receipt(api_id, "1.0.0", "ERROR", started, input_data=dict(params),
                                error_code="TOOLBOX_OPERATION_FAILED", error=str(error),
                                recovery_hint="Inspect inputs and the registered tool source; do not retry a scene write blindly.")
