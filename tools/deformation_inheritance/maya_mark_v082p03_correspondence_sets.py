import json
from pathlib import Path

import maya.cmds as cmds


SETS_PATH = Path(
    r"Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\.info\a_weight_transfer_v082_reboot\param_sweep\v082p03_energy_n100_maya_diagnostic_sets.json"
)


SET_NAME_MAP = {
    "unsupported": "CDFDIAG_V082P03_A_UNSUPPORTED_SET",
    "low_confidence_actionable": "CDFDIAG_V082P03_A_LOWCONF_ACTIONABLE_SET",
    "strict_block": "CDFDIAG_V082P03_A_STRICT_BLOCK_SET",
    "distance_low": "CDFDIAG_V082P03_A_DISTANCE_LOW_SET",
    "normal_mismatch": "CDFDIAG_V082P03_A_NORMAL_MISMATCH_SET",
    "semantic_ambiguous": "CDFDIAG_V082P03_A_SEMANTIC_AMBIGUOUS_SET",
    "low_family_conf": "CDFDIAG_V082P03_A_LOW_FAMILY_CONF_SET",
    "topology_discontinuity": "CDFDIAG_V082P03_A_TOPOLOGY_DISCONTINUITY_SET",
    "face_semantic_discontinuity": "CDFDIAG_V082P03_A_FACE_SEMANTIC_DISCONTINUITY_SET",
    "high_confidence_supported": "CDFDIAG_V082P03_A_HIGHCONF_SUPPORTED_SET",
}


def _target_mesh():
    hits = cmds.ls("A", long=True) or cmds.ls("|A", long=True) or []
    if not hits:
        raise RuntimeError("Target mesh A not found in current Maya scene.")
    return hits[0]


def _delete_if_exists(name):
    if cmds.objExists(name):
        cmds.delete(name)


def _component_chunks(mesh, indices, chunk_size=900):
    for start in range(0, len(indices), chunk_size):
        batch = indices[start : start + chunk_size]
        yield ["%s.vtx[%d]" % (mesh, int(i)) for i in batch]


def _make_set(mesh, set_name, indices):
    _delete_if_exists(set_name)
    result_set = cmds.sets(empty=True, name=set_name)
    for comps in _component_chunks(mesh, indices):
        if comps:
            cmds.sets(comps, add=result_set)
    return result_set


def main():
    payload = json.loads(SETS_PATH.read_text(encoding="utf-8"))
    mesh = _target_mesh()
    sets = payload.get("sets", {})
    created = {}
    cmds.undoInfo(openChunk=True, chunkName="CDFDIAG_V082P03_mark_correspondence_sets")
    try:
        for key, set_name in SET_NAME_MAP.items():
            indices = sets.get(key, [])
            created[set_name] = {
                "key": key,
                "count": len(indices),
                "set": _make_set(mesh, set_name, indices),
            }
        default_set = SET_NAME_MAP["low_confidence_actionable"]
        cmds.select(default_set, replace=True)
    finally:
        cmds.undoInfo(closeChunk=True)
    return {
        "status": "SUCCESS",
        "scene": cmds.file(q=True, sn=True),
        "target_mesh": mesh,
        "sets_path": str(SETS_PATH),
        "default_selected_set": default_set,
        "created": created,
        "note": "V082P03 energy correspondence diagnostic sets only; no skin weights were written.",
    }


result = main()
