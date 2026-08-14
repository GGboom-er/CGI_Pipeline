"""Bounded service-side audit for public MCP tool calls."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


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


def _result_status(result: Any) -> str:
    if bool(getattr(result, "isError", False)):
        return "ERROR"
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, Mapping) and structured.get("status"):
        return str(structured["status"]).upper()
    return "SUCCESS"


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
        started_at = datetime.now(timezone.utc)
        started_clock = time.perf_counter()
        record = {
            "schema_version": 1,
            "timestamp": started_at.isoformat(),
            "tool": str(getattr(context.message, "name", "unknown")),
            "request_id": _context_value(context, "request_id"),
            "session_id": _context_value(context, "session_id"),
            "client_id": _context_value(context, "client_id"),
            "transport": _context_value(context, "transport"),
            "correlation": _correlation_metadata(context.message),
        }
        try:
            result = await call_next(context)
        except Exception as exc:
            record.update(
                status="ERROR",
                duration_ms=round((time.perf_counter() - started_clock) * 1000, 2),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self._write_safely(record)
            raise

        record.update(
            status=_result_status(result),
            duration_ms=round((time.perf_counter() - started_clock) * 1000, 2),
            error_type=None,
            error=None,
        )
        self._write_safely(record)
        return result


def build_mcp_audit(project_root: Path) -> McpCallAuditMiddleware:
    return McpCallAuditMiddleware(
        default_audit_path(project_root),
        max_bytes=_env_int("CGI_MCP_AUDIT_MAX_BYTES", 5 * 1024 * 1024),
        backup_count=_env_int("CGI_MCP_AUDIT_BACKUPS", 3),
    )
