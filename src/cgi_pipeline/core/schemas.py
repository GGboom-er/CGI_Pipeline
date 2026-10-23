"""Pydantic boundary schemas for API and API-chain submissions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator


def _catalog_for_payload(payload, info):
    from cgi_pipeline.catalog import snapshot

    selected = (info.context or {}).get('catalog_snapshot')
    if selected is None:
        selected = snapshot()
    expected = (getattr(payload, 'api_context', None) or {}).get('expected_catalog_version')
    if expected is not None and expected != selected[1]:
        raise ValueError('CATALOG_VERSION_STALE: capability catalog changed before validation')
    return selected


class ApiPayloadSchema(BaseModel):
    """Validate one API invocation before a DCC process is started."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    api_id: str = Field(..., min_length=1)
    project: str = Field(default="default", min_length=1)
    asset_name: str = Field(default="untitled", min_length=1)
    source_path: str = Field(default="")
    api_params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def reject_removed_transport_fields(cls, values):
        if isinstance(values, dict):
            removed = sorted({"params", "parameters", "context"} & set(values))
            if removed:
                raise ValueError(
                    "removed API transport fields: "
                    f"{', '.join(removed)}; use api_params/api_context"
                )
        return values

    @model_validator(mode="after")
    def validate_api_params(self, info: ValidationInfo):
        from cgi_pipeline.core.capability_registry import resolve_api_id
        from cgi_pipeline.contracts import ApiContractError, validate_params

        try:
            selected = _catalog_for_payload(self, info)
            self.api_id = resolve_api_id(self.api_id, catalog_snapshot=selected)
            spec = selected[0][self.api_id]
            normalized = validate_params(spec, dict(self.api_params or {}))
        except (KeyError, ApiContractError) as exc:
            raise ValueError(str(exc)) from exc
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
    def validate_chain(self, info: ValidationInfo):
        from cgi_pipeline.core.capability_registry import resolve_api_id
        from cgi_pipeline.contracts import ApiContractError, validate_params

        selected = _catalog_for_payload(self, info)
        for index, step in enumerate(self.api_chain):
            api_id = step.get("api_id")
            if not api_id:
                raise ValueError(f"API chain step {index} missing api_id")
            try:
                canonical_id = resolve_api_id(str(api_id), catalog_snapshot=selected)
                step["api_id"] = canonical_id
                spec = selected[0][canonical_id]
                params = validate_params(spec, step.get("parameters") or {})
            except (KeyError, ApiContractError) as exc:
                raise ValueError(f"step {index} ({api_id}): {exc}") from exc
            step["parameters"] = params
        return self
