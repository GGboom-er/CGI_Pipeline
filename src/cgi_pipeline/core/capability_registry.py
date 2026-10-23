"""Runtime indexes derived from the single capability catalog."""

from __future__ import annotations

import importlib
from copy import deepcopy

from cgi_pipeline import catalog as _catalog_module
from cgi_pipeline import contracts as _contracts_module


def reload() -> None:
    # Long-lived workers may still hold an older status/input contract while
    # the generated catalog has already changed. Refresh the validator before
    # accepting the new catalog so strict validation remains deterministic.
    error_type = _contracts_module.ApiContractError
    importlib.reload(_contracts_module)
    # Modules such as server.internals import this exception at startup. Keep
    # its identity stable while refreshing the contract functions/constants.
    _contracts_module.ApiContractError = error_type
    _catalog_module.reload()


def get_all_capabilities() -> list[dict]:
    specs, _ = _catalog_module.snapshot()
    return [deepcopy(specs[key]) for key in sorted(specs)]


def get_capability_map() -> dict[str, dict]:
    return deepcopy(_catalog_module.snapshot()[0])


def get_executor(api_id: str) -> str:
    try:
        return _catalog_module.snapshot()[0][api_id]["executor"]
    except KeyError as exc:
        raise KeyError(f"unknown api_id: {api_id}") from exc


def resolve_api_id(value: str, *, catalog_snapshot=None) -> str:
    specs, _ = _catalog_module.snapshot() if catalog_snapshot is None else catalog_snapshot
    if value in specs:
        return value
    matches = [api_id for api_id in specs if api_id.rsplit(".", 1)[-1] == value]
    if len(matches) == 1:
        return matches[0]
    raise KeyError(f"unknown or ambiguous api id: {value}")


def get_skip_audit_capabilities() -> set[str]:
    specs, _ = _catalog_module.snapshot()
    return {api_id for api_id, spec in specs.items() if spec.get("skip_audit", False)}
