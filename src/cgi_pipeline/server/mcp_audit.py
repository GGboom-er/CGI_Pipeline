"""Bounded service-side audit for public MCP tool calls."""

from __future__ import annotations

import json
import asyncio
from contextlib import ExitStack
import logging
import os
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from cgi_pipeline.server.http_lifecycle import lifecycle, LifecycleError


_LOGGER = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()
_CORRELATION_KEYS = {
    "client_id",
    "conversation_id",
    "correlation_id",
    "invocation_id",
    "progress_token",
    "request_id",
    "session_id",
    "trace_id",
}
_AUDIT_ID_KEYS = ("api_id", "workflow_id", "task_id", "workspace_id", "business_task_id")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        _LOGGER.warning("Ignoring invalid integer %s=%r", name, os.getenv(name))
        return default


def default_audit_path(project_root: Path) -> Path:
    configured = os.getenv("CGI_MCP_AUDIT_PATH")
    return Path(configured) if configured else project_root / "audit" / "mcp_calls.jsonl"


def _context_value(context: MiddlewareContext[Any], name: str) -> str | None:
    fastmcp_context = context.fastmcp_context
    if fastmcp_context is None:
        return None
    try:
        value = getattr(fastmcp_context, name)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return None if value is None else str(value)


def _correlation_metadata(message: Any) -> dict[str, Any]:
    meta = getattr(message, "meta", None)
    if meta is None:
        return {}
    if hasattr(meta, "model_dump"):
        raw = meta.model_dump(mode="json", by_alias=True)
    elif isinstance(meta, Mapping):
        raw = dict(meta)
    else:
        return {}

    result = {}
    for key, value in raw.items():
        normalized = str(key).lstrip("_").replace("-", "_")
        if normalized == "progressToken":
            normalized = "progress_token"
        if normalized in _CORRELATION_KEYS and value is not None:
            result[normalized] = value
    return result


def _call_identifiers(message: Any) -> dict[str, str]:
    arguments = getattr(message, "arguments", None)
    if not isinstance(arguments, Mapping):
        return {}
    nested = arguments.get("params")
    nested = nested if isinstance(nested, Mapping) else {}

    result = {}
    for key in _AUDIT_ID_KEYS:
        value = arguments.get(key, nested.get(key))
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()[:256]
    return result


def _result_status(result: Any) -> str:
    if bool(getattr(result, "isError", False)):
        return "ERROR"
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, Mapping) and structured.get("status"):
        return str(structured["status"]).upper()
    return "SUCCESS"


def _result_metadata(result: Any) -> dict:
    """Whitelist returned links and diagnostics; never copy params, code, outputs or logs."""
    data = getattr(result, 'structured_content', None)
    if not isinstance(data, Mapping):
        return {}
    record = {key: value[:256] for key in _AUDIT_ID_KEYS
              if isinstance((value := data.get(key)), str) and value}
    receipt = data.get('receipt') or data.get('detail')
    receipt = receipt if isinstance(receipt, Mapping) else {}
    for key in ('error_code', 'error_type'):
        value = data.get(key) or receipt.get(key)
        if isinstance(value, (str, int)) and value != '':
            record[key] = str(value)[:128]
    # Keep arbitrary error text in its existing receipt, not a second privacy-sensitive log.
    if data.get('error') or receipt.get('error'):
        record['error'] = 'Business error; inspect the linked task receipt.'
    timings = data.get('timings_ms')
    if isinstance(timings, Mapping):
        record['timings_ms'] = {key: round(value, 2) for key in ('dispatch_return_ms', 'recovery_wait_ms')
                                if isinstance((value := timings.get(key)), (int, float))}
    elapsed = receipt.get('elapsed_sec')
    if isinstance(elapsed, (int, float)):
        record.setdefault('timings_ms', {})['api_execution_ms'] = round(elapsed * 1000, 2)
    return record


def _rotate(path: Path, max_bytes: int, backup_count: int, incoming: int) -> None:
    if not path.exists() or path.stat().st_size + incoming <= max_bytes:
        return
    for index in range(backup_count, 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        target = path.with_name(f"{path.name}.{index}")
        if not source.exists():
            continue
        if target.exists():
            target.unlink()
        source.replace(target)


class McpCallAuditMiddleware(Middleware):
    """Record one compact JSON line for every public MCP tool call."""

    def __init__(
        self,
        audit_path: Path,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 3,
    ) -> None:
        self.audit_path = Path(audit_path)
        self.max_bytes = max(1, max_bytes)
        self.backup_count = max(1, backup_count)

    def _append(self, record: Mapping[str, Any]) -> None:
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        incoming = len(payload.encode("utf-8"))
        with _WRITE_LOCK:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(self.audit_path, self.max_bytes, self.backup_count, incoming)
            with self.audit_path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(payload)

    def _write_safely(self, record: Mapping[str, Any]) -> None:
        try:
            self._append(record)
        except OSError as exc:
            _LOGGER.warning("MCP call audit write failed: %s", exc)

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        # A disconnected/cancelled caller must not release ownership while the
        # accepted invocation, its thread work, or receipt audit is still active.
        task = asyncio.create_task(self._owned_call(context, call_next))
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    async def _owned_call(self, context, call_next):
        with ExitStack() as owned:
            try:
                owned.enter_context(lifecycle.activity('mcp:' + str(context.message.name)))
            except LifecycleError as exc:
                return ToolResult(structured_content={
                    'status': 'ERROR', 'error_code': exc.code, 'submitted': False,
                    'recovery_hint': 'HTTP frontend is updating; retry after it resumes. No work was submitted.',
                })
            return await self._audited_call(context, call_next)

    async def _audited_call(self, context, call_next):
        started_at = datetime.now(timezone.utc)
        started_clock = time.perf_counter()
        record = {
            "schema_version": 2,
            "timestamp": started_at.isoformat(),
            "tool": str(getattr(context.message, "name", "unknown")),
            "request_id": _context_value(context, "request_id"),
            "session_id": _context_value(context, "session_id"),
            "client_id": _context_value(context, "client_id"),
            "transport": _context_value(context, "transport"),
            "correlation": _correlation_metadata(context.message),
        }
        record.update(_call_identifiers(context.message))
        try:
            result = await call_next(context)
        except Exception as exc:
            record.update(
                status="ERROR",
                duration_ms=round((time.perf_counter() - started_clock) * 1000, 2),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            await lifecycle.run_sync(self._write_safely, record)
            raise

        record.update(
            status=_result_status(result),
            duration_ms=round((time.perf_counter() - started_clock) * 1000, 2),
            error_type=None,
            error=None,
        )
        record.update(_result_metadata(result))
        await lifecycle.run_sync(self._write_safely, record)
        return result


def build_mcp_audit(project_root: Path) -> McpCallAuditMiddleware:
    return McpCallAuditMiddleware(
        default_audit_path(project_root),
        max_bytes=_env_int("CGI_MCP_AUDIT_MAX_BYTES", 5 * 1024 * 1024),
        backup_count=_env_int("CGI_MCP_AUDIT_BACKUPS", 3),
    )
