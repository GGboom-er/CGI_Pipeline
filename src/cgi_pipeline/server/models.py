"""Pydantic input models for CGI's public MCP tools."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


MAX_BATCH_CALLS = 100


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_project: Optional[str] = Field(default=None, min_length=1, max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        description="Workspace storage namespace, independent of business project rules. Use default for a task sandbox; omitted preserves the existing project-based layout.")
    workspace_id: Optional[str] = Field(default=None, min_length=1, description="Reuse the returned workspace_id with its recorded storage namespace in workspace_project; project continues to select business rules.")
    business_task_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description=(
            "Stable external task id, such as PL251. The first call creates "
            "projects/{workspace_project}/{business_task_id}; omitted workspace_project uses project. Later calls reuse the same storage identity."
        ),
    )
    scene_action: Literal['continue', 'open'] = Field(default='continue', description="continue reuses the live scene without staging/opening/replaying APIs. open explicitly initializes/replaces the background scene from source_path (empty means new scene). Lost scenes require explicit open.")


class TaskQueryInput(BaseModel):
    """Recover one previously submitted CGI task."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    task_id: str = Field(..., min_length=1, description="Task id returned by execute_api or pipeline_execute_workflow.")
    wait_sec: Optional[int] = Field(default=None, ge=0, description="Seconds to wait server-side for a terminal state; omitted or larger values use the client-safe cap (CGI_MCP_MAX_WAIT_SEC, default 110). 0 returns the current state at once.")
    detail: bool = Field(default=False, description="False returns a summary of at most 4 KiB (status, artifacts, errors, small output fields, receipt_ref); True returns the full receipt.")


class ExecuteApiInput(WorkspaceInput):
    """Execute one deterministic API from the manifest catalog."""

    model_config = ConfigDict(str_strip_whitespace=True)
    api_id: str = Field(..., min_length=1, description="Catalog API id returned by list_apis.")
    expected_catalog_version: Optional[str] = Field(
        default=None,
        min_length=1,
        description=(
            "Catalog version cached from list_apis or api_help. Reuse it to execute a known "
            "contract directly; stale versions are rejected before a DCC starts."
        ),
    )
    params: dict = Field(default_factory=dict, description="API-specific parameters documented by api_help(api_id).")
    project: str = Field(default="default", description="Project configuration key.")
    asset_name: str = Field(default="untitled", description="Asset name for run reports and project resolution.")
    source_path: str = Field(default="", description="Optional source scene path for APIs that operate on a scene.")
    execution_mode: str = Field(default="background", description="background workspace-affine runtime or foreground DCC session, when supported by the API.")
    foreground_port: Optional[int] = Field(default=None, description="Explicit Maya or Blender foreground port when required by api_help.")
    wait: bool = Field(default=True, description="Wait for the final receipt by default; False returns a recoverable task id.")
    wait_timeout_sec: Optional[int] = Field(default=None, ge=1, description="Seconds to wait before returning PROGRESS; omitted or larger values are capped below the client tool timeout (CGI_MCP_MAX_WAIT_SEC, default 110). Continue with get_task_result, which waits again server-side.")
    poll_interval_sec: float = Field(default=0.25, gt=0, description="Task-result polling interval while waiting.")
    detail: bool = Field(default=False, description="False returns a summary of at most 4 KiB (status, artifacts, errors, small output fields, receipt_ref); True returns the full receipt.")


class ExecuteWorkflowInput(WorkspaceInput):
    """Run one registered API composition."""

    model_config = ConfigDict(str_strip_whitespace=True)
    workflow_id: str = Field(..., min_length=1, description="Registered workflow id from list_workflows.")
    source_path: str = Field(default="", description="Optional source scene path.")
    project: str = Field(default="default", description="Project configuration key.")
    asset_name: str = Field(default="untitled", description="Asset name for run reports and project resolution.")
    extra_params: Optional[dict] = Field(default=None, description="Workflow input values used by its declared templates.")
    wait: bool = Field(default=True, description="Wait for the final workflow result by default; for long workflows, False returns a task_id that get_task_result resumes without resubmission.")
    wait_timeout_sec: Optional[int] = Field(default=None, ge=1, description="Seconds to wait before returning PROGRESS; omitted or larger values are capped below the client tool timeout (CGI_MCP_MAX_WAIT_SEC, default 110). Continue with get_task_result, which waits again server-side.")
    poll_interval_sec: float = Field(default=0.25, gt=0, description="Task-result polling interval while waiting.")
    detail: bool = Field(default=False, description="False returns a summary of at most 4 KiB (status, artifacts, errors, small output fields, receipt_ref); True returns the full receipt.")


class ExecuteBatchInput(WorkspaceInput):
    """Execute literal API calls in one DCC session/IPC boundary."""
    model_config = ConfigDict(str_strip_whitespace=True)
    calls: list[dict] = Field(
        ...,
        min_length=1,
        max_length=MAX_BATCH_CALLS,
        description=f"API calls: [{{api_id, params}}]. Literal params only; maximum {MAX_BATCH_CALLS} calls.",
    )
    expected_catalog_version: Optional[str] = Field(
        default=None,
        min_length=1,
        description=(
            "Catalog version cached from list_apis or api_help. Stale batches are rejected "
            "before validation or DCC startup."
        ),
    )
    source_path: str = Field(default="")
    project: str = Field(default="default")
    asset_name: str = Field(default="untitled")
    execution_mode: str = Field(default="background", description="background 或 foreground。前台必须指定 foreground_port。")
    foreground_port: Optional[int] = Field(default=None)
    atomic: bool = Field(default=True, description="True stops on first failure; False continues all calls. Maya rolled_back only confirms an undo attempt succeeded, not disk/external side effects. Inspect batch counts and every receipt; batches are never automatically replayed.")
    wait: bool = Field(default=True)
    wait_timeout_sec: Optional[int] = Field(default=None, ge=1, description="Seconds to wait before returning PROGRESS; omitted or larger values are capped below the client tool timeout (CGI_MCP_MAX_WAIT_SEC, default 110). Continue with get_task_result, which waits again server-side.")
    poll_interval_sec: float = Field(default=0.25, gt=0)
    detail: bool = Field(default=False, description="False returns a summary of at most 4 KiB (status, artifacts, errors, small output fields, receipt_ref); True returns the full receipt.")
