"""Pydantic boundary schemas for API and API-chain submissions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiPayloadSchema(BaseModel):
    """Validate one API invocation before a DCC process is started."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    api_id: str = Field(..., min_length=1)
    project: str = Field(default="default", min_length=1)
    asset_name: str = Field(default="untitled", min_length=1)
    source_path: str = Field(default="")
    params: dict[str, Any] = Field(default_factory=dict)
    api_params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_api_params(self):
        from core.api_registry import resolve_api_id
        from api.registry import get_api
        from api.contract import ApiContractError, validate_params

        try:
            self.api_id = resolve_api_id(self.api_id)
            spec = get_api(self.api_id)
            merged = dict(self.params or self.api_params or {})
            normalized = validate_params(spec, merged)
        except (KeyError, ApiContractError) as exc:
            raise ValueError(str(exc)) from exc
        self.params = normalized
        self.api_params = normalized
        return self


class ApiChainPayloadSchema(BaseModel):
    """Validate an ordered chain of API steps."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    task_id: str = Field(..., min_length=1)
    source_path: str = Field(default="")
    project: str = Field(default="default", min_length=1)
    asset_name: str = Field(default="untitled", min_length=1)
    api_chain: list[dict[str, Any]] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_chain(self):
        from core.api_registry import resolve_api_id
        from api.registry import get_api
        from api.contract import ApiContractError, validate_params

        for index, step in enumerate(self.api_chain):
            api_id = step.get("api_id")
            if not api_id:
                raise ValueError(f"API chain step {index} missing api_id")
            try:
                canonical_id = resolve_api_id(str(api_id))
                step["api_id"] = canonical_id
                spec = get_api(canonical_id)
                params = validate_params(spec, step.get("parameters") or {})
            except (KeyError, ApiContractError) as exc:
                raise ValueError(f"step {index} ({api_id}): {exc}") from exc
            step["parameters"] = params
        return self
