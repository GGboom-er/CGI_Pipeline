"""Pydantic input models for CGI's six public MCP tools."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TaskQueryInput(BaseModel):
    """Recover one previously submitted CGI task."""

    model_config = ConfigDict(str_strip_whitespace=True)
    task_id: str = Field(..., min_length=1, description="Task id returned by execute_api or pipeline_execute_workflow.")


class ExecuteApiInput(BaseModel):
    """Execute one deterministic API from the manifest catalog."""

    model_config = ConfigDict(str_strip_whitespace=True)
    api_id: str = Field(..., min_length=1, description="Catalog API id returned by list_apis.")
    params: dict = Field(default_factory=dict, description="API-specific parameters documented by api_help(api_id).")
    project: str = Field(default="default", description="Project configuration key.")
    asset_name: str = Field(default="untitled", description="Asset name for run reports and project resolution.")
    source_path: str = Field(default="", description="Optional source scene path for APIs that operate on a scene.")
    execution_mode: str = Field(default="background", description="background Worker or foreground DCC session, when supported by the API.")
    foreground_port: Optional[int] = Field(default=None, description="Explicit Maya or Blender foreground port when required by api_help.")
    wait: bool = Field(default=True, description="Wait for the final receipt by default; False returns a recoverable task id.")
    wait_timeout_sec: int = Field(default=1800, ge=1, description="Maximum seconds to wait before returning PROGRESS.")
    poll_interval_sec: float = Field(default=0.25, gt=0, description="Task-result polling interval while waiting.")


class ExecuteWorkflowInput(BaseModel):
    """Run one registered API composition."""

    model_config = ConfigDict(str_strip_whitespace=True)
    workflow_id: str = Field(..., min_length=1, description="Registered workflow id from list_workflows.")
    source_path: str = Field(default="", description="Optional source scene path.")
    project: str = Field(default="default", description="Project configuration key.")
    asset_name: str = Field(default="untitled", description="Asset name for run reports and project resolution.")
    extra_params: Optional[dict] = Field(default=None, description="Workflow input values used by its declared templates.")
    wait: bool = Field(default=True, description="Wait for the final workflow result by default.")
    wait_timeout_sec: int = Field(default=1800, ge=1, description="Maximum seconds to wait before returning PROGRESS.")
    poll_interval_sec: float = Field(default=0.25, gt=0, description="Task-result polling interval while waiting.")
