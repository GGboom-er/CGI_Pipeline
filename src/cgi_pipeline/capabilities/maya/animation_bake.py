"""Bounded Maya animation baking."""

from __future__ import annotations

import math
import time
from typing import Any

from cgi_pipeline.capabilities.maya.animation_core import (
    _cmds, _limit, _long, _mutate, _number, _receipt,
)
from cgi_pipeline.contracts import ApiContext


_MAX_TARGET_SAMPLES = 65536
_MAX_SIMULATION_SAMPLES = 16384


def _attributes(cmds, nodes: list[str], values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("attributes must contain at least one attribute")
    if not all(isinstance(value, str) and value and not value.startswith(".") for value in values):
        raise ValueError("attributes must contain non-empty relative plug names")
    if len(set(values)) != len(values):
        raise ValueError("attributes must be unique")
    missing = [f"{node}.{attribute}" for node in nodes for attribute in values
               if not cmds.objExists(f"{node}.{attribute}")]
    if missing:
        raise ValueError(f"Attribute does not exist: {missing[0]}")
    return list(values)


def _key_count(cmds, plug: str, time_range: tuple[float, float]) -> int:
    value = cmds.keyframe(plug, query=True, keyframeCount=True, time=time_range) or 0
    return int(value)


def bake_animation(params: dict, context: ApiContext) -> dict:
    started = time.perf_counter()
    cmds = _cmds(context)

    def run():
        if params.get("action") != "bake":
            raise ValueError("Unsupported bake action")
        maximum = _limit(params)
        raw_nodes = params.get("nodes") or []
        if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= maximum:
            raise ValueError("nodes must be a non-empty array within max_items")
        nodes = [_long(cmds, node) for node in raw_nodes]
        if len(set(nodes)) != len(nodes):
            raise ValueError("nodes must be unique")
        attributes = _attributes(cmds, nodes, params.get("attributes"))
        if len(attributes) > maximum:
            raise ValueError("attributes exceeds max_items")
        start = _number(params.get("start_time"), "start_time")
        end = _number(params.get("end_time"), "end_time")
        if start > end:
            raise ValueError("start_time must not exceed end_time")
        sample = _number(params.get("sample_by", 1.0), "sample_by")
        if sample <= 0:
            raise ValueError("sample_by must be greater than zero")
        samples = int(math.floor((end - start) / sample)) + 1
        target_samples = len(nodes) * len(attributes) * samples
        simulation = params.get("simulation", False)
        if not isinstance(simulation, bool):
            raise ValueError("simulation must be boolean")
        cap = _MAX_SIMULATION_SAMPLES if simulation else _MAX_TARGET_SAMPLES
        if target_samples > cap:
            raise ValueError(f"bake workload must not exceed {cap} target samples")
        time_range = (start, end)
        plugs = [f"{node}.{attribute}" for node in nodes for attribute in attributes]
        before = {plug: _key_count(cmds, plug, time_range) for plug in plugs}

        def apply():
            changed = int(cmds.bakeResults(
                nodes,
                animation="objects",
                attribute=attributes,
                time=time_range,
                sampleBy=sample,
                simulation=simulation,
                disableImplicitControl=params.get("disable_implicit_control", False),
                preserveOutsideKeys=params.get("preserve_outside_keys", True),
                sparseAnimCurveBake=params.get("sparse", False),
                minimizeRotation=params.get("minimize_rotation", True),
                hierarchy="none",
                shape=False,
                controlPoints=False,
            ) or 0)
            if changed <= 0:
                raise RuntimeError("Maya did not bake any animation channels")
            after = {plug: _key_count(cmds, plug, time_range) for plug in plugs}
            missing = [plug for plug, count in after.items() if count <= 0]
            if missing:
                raise RuntimeError(f"Bake readback found no keys on: {missing[0]}")
            return changed, after

        changed, after = _mutate(cmds, "cgi_animation_bake", apply)
        return {
            "action": "bake", "nodes": nodes, "attributes": attributes,
            "time": [start, end], "sample_by": sample,
            "simulation": simulation, "changed_channels": changed,
            "target_samples": target_samples,
            "items": [{"plug": plug, "before_keys": before[plug],
                       "after_keys": after[plug]} for plug in plugs],
        }

    return _receipt(context, "maya.animation.bake", started, params, run)
