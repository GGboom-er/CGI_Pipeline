"""Generic Maya reference planning and update engine.

This module is the CGI API owner. Project-specific latest-version resolution
must be injected as a resolver callable.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


TargetResolver = Callable[[str], tuple[str, str]]


def _maya_cmds():
    import maya.cmds as cmds

    return cmds


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    )


def _reference_path(cmds: Any, reference_node: str) -> str:
    return str(
        cmds.referenceQuery(
            reference_node,
            filename=True,
            withoutCopyNumber=True,
        )
        or ""
    )


def build_update_plan(
    resolve_target: TargetResolver,
    cmds_module: Any = None,
    skip_status: str = "SKIP_NOT_TARGET",
) -> list[dict[str, Any]]:
    """Build a read-only plan using a project resolver: old path -> (asset, new path)."""
    cmds = cmds_module or _maya_cmds()
    rows: list[dict[str, Any]] = []
    reference_nodes = sorted(
        node
        for node in (cmds.ls(type="reference") or [])
        if node != "sharedReferenceNode"
    )

    for reference_node in reference_nodes:
        row: dict[str, Any] = {
            "referenceNode": reference_node,
            "namespace": "",
            "loaded": False,
            "asset": "",
            "oldPath": "",
            "newPath": "",
            "status": "SKIP_ERROR",
        }
        try:
            row["oldPath"] = _reference_path(cmds, reference_node)
            row["namespace"] = str(
                cmds.referenceQuery(reference_node, namespace=True) or ""
            )
            row["loaded"] = bool(
                cmds.referenceQuery(reference_node, isLoaded=True)
            )
            row["asset"], row["newPath"] = resolve_target(row["oldPath"])
            if not row["asset"]:
                row["status"] = skip_status
            elif not row["newPath"]:
                row["status"] = "SKIP_NO_VERSION"
            elif _same_path(row["oldPath"], row["newPath"]):
                row["status"] = "CURRENT"
            else:
                row["status"] = "UPDATE"
        except RuntimeError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def _failed_edit_count(cmds: Any, reference_node: str) -> int | None:
    try:
        edits = cmds.referenceQuery(
            reference_node,
            editStrings=True,
            failedEdits=True,
            successfulEdits=False,
        )
        return len(edits or [])
    except RuntimeError:
        return None


def _print_rows(rows: list[dict[str, Any]], title: str) -> None:
    print("\n%s: %d reference node(s)" % (title, len(rows)))
    for row in rows:
        print(
            "[{status}] {referenceNode} | {namespace} | loaded={loaded} | "
            "{oldPath} -> {newPath}".format(**row)
        )
        if row.get("error"):
            print("  ERROR: %s" % row["error"])
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("Summary: %s" % counts)


def preview(
    resolve_target: TargetResolver,
    cmds_module: Any = None,
    skip_status: str = "SKIP_NOT_TARGET",
    title: str = "Maya reference update",
) -> list[dict[str, Any]]:
    rows = build_update_plan(resolve_target, cmds_module, skip_status)
    _print_rows(rows, title)
    return rows


def apply_updates(
    resolve_target: TargetResolver,
    cmds_module: Any = None,
    skip_status: str = "SKIP_NOT_TARGET",
    title: str = "Maya reference update",
    undo_name: str = "Update References",
) -> list[dict[str, Any]]:
    cmds = cmds_module or _maya_cmds()
    rows = build_update_plan(resolve_target, cmds, skip_status)
    cmds.undoInfo(openChunk=True, chunkName=undo_name)
    try:
        for row in rows:
            if row["status"] != "UPDATE":
                continue
            try:
                kwargs = {"loadReference": row["referenceNode"]}
                if not row["loaded"]:
                    kwargs["loadReferenceDepth"] = "none"
                cmds.file(row["newPath"], **kwargs)

                actual_path = _reference_path(cmds, row["referenceNode"])
                actual_namespace = str(
                    cmds.referenceQuery(row["referenceNode"], namespace=True)
                    or ""
                )
                actual_loaded = bool(
                    cmds.referenceQuery(row["referenceNode"], isLoaded=True)
                )
                row["actualPath"] = actual_path
                row["actualNamespace"] = actual_namespace
                row["failedEdits"] = _failed_edit_count(
                    cmds, row["referenceNode"]
                )
                if not _same_path(actual_path, row["newPath"]):
                    row["status"] = "VERIFY_PATH_FAILED"
                elif actual_namespace != row["namespace"]:
                    row["status"] = "VERIFY_NAMESPACE_FAILED"
                elif actual_loaded != row["loaded"]:
                    row["status"] = "VERIFY_LOAD_STATE_FAILED"
                else:
                    row["status"] = "UPDATED"
            except RuntimeError as exc:
                row["status"] = "ERROR"
                row["error"] = str(exc)
    finally:
        cmds.undoInfo(closeChunk=True)

    _print_rows(rows, title)
    return rows
