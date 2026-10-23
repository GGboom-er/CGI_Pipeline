"""Authenticated local HTTP controls; readable runtime files grant management."""

from __future__ import annotations

import functools
import hmac
import json
import logging
import math
import os
import secrets
import uuid
from pathlib import Path

import psutil
from filelock import FileLock, Timeout
from starlette.responses import JSONResponse
from starlette.routing import Route

from cgi_pipeline.server.http_lifecycle import LifecycleError


_LOGGER = logging.getLogger(__name__)


class HttpControl:
    """One frontend generation's control routes and atomic credential metadata.

    The managed runtime directory's readers are trusted administrators. Mode
    0600 is used when creating metadata; it does not promise Windows DACL isolation.
    No secret is returned by these routes or supplied on a process command line.
    """

    def __init__(self, lifecycle, metadata_path, on_stop, *, generation=None,
                 process_start_time=None):
        self.lifecycle = lifecycle
        self.metadata_path = Path(metadata_path)
        self._metadata_lock = FileLock(str(self.metadata_path) + ".lock", timeout=30)
        self.on_stop = on_stop
        self._secret = secrets.token_hex(32)
        self.identity = {
            "protocol_version": 1,
            "pid": os.getpid(),
            "process_start_time": (psutil.Process().create_time() if process_start_time is None
                                   else process_start_time),
            "generation": generation or str(uuid.uuid4()),
        }

    def write_metadata(self):
        """Fail startup if credential publication fails; never expose unauthenticated control."""
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with self._metadata_lock:
            current = self._read_metadata()
            if current is not None:
                self._refuse_live_owner(current)
            temporary = self.metadata_path.with_name(f".{self.metadata_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({**self.identity, "secret": self._secret}, stream, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.metadata_path)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    _LOGGER.warning("HTTP control temporary cleanup failed (%s)", type(exc).__name__)

    def _read_metadata(self):
        try:
            current = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            valid = (isinstance(current, dict) and set(current) == {*self.identity, "secret"}
                     and type(current["protocol_version"]) is int and current["protocol_version"] == 1
                     and type(current["pid"]) is int and 0 < current["pid"] <= 2147483647
                     and type(current["process_start_time"]) in (int, float)
                     and math.isfinite(current["process_start_time"]) and current["process_start_time"] > 0
                     and isinstance(current["generation"], str)
                     and isinstance(current["secret"], str) and len(current["secret"]) >= 32
                     and current["secret"].isascii())
            if not valid:
                raise ValueError("invalid metadata identity")
            uuid.UUID(current["generation"])
            return current
        except FileNotFoundError:
            return None
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise LifecycleError("HTTP_CONTROL_METADATA_INVALID", "Existing control metadata identity is invalid.") from None

    @staticmethod
    def _refuse_live_owner(current):
        try:
            actual_start = psutil.Process(current["pid"]).create_time()
        except psutil.NoSuchProcess:
            return
        except (psutil.Error, OSError, ValueError, OverflowError):
            raise LifecycleError("HTTP_CONTROL_OWNER_UNKNOWN", "Existing control owner cannot be verified.") from None
        if abs(actual_start - current["process_start_time"]) <= 0.02:
            raise LifecycleError("HTTP_CONTROL_OWNER_ALIVE", "An active frontend already owns this control file.")

    def close(self):
        """A closing old instance must not remove a newer generation's credentials."""
        try:
            with self._metadata_lock:
                current = self._read_metadata()
                if current is not None and all(current.get(key) == value for key, value in self.identity.items()):
                    self.metadata_path.unlink()
        except FileNotFoundError:
            return
        except (OSError, LifecycleError, Timeout) as exc:
            _LOGGER.warning("HTTP control metadata cleanup failed (%s)", type(exc).__name__)

    def routes(self):
        return [Route(f"/_cgi/control/{action}", functools.partial(self._handle, action=action),
                      methods=["GET" if action == "status" else "POST"])
                for action in ("status", "drain", "resume", "stop")]

    def _authenticate(self, request):
        raw = request.scope.get("headers", [])
        if any(key.lower() == b"origin" for key, _ in raw):
            raise LifecycleError("HTTP_CONTROL_ORIGIN", "Origin-bearing control requests are refused.")
        tokens = [value for key, value in raw if key.lower() == b"x-cgi-control-token"]
        if len(tokens) != 1 or not hmac.compare_digest(tokens[0], self._secret.encode("ascii")):
            raise LifecycleError("HTTP_CONTROL_AUTH", "Control authentication failed.")

    async def _parameters(self, request, action):
        if action == "status":
            if set(request.query_params) != {"generation"}:
                raise LifecycleError("HTTP_CONTROL_INVALID", "generation query parameter is required.")
            parameters = dict(request.query_params)
        else:
            if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                raise LifecycleError("HTTP_CONTROL_INVALID", "A JSON control body is required.")
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 4096:
                    raise LifecycleError("HTTP_CONTROL_INVALID", "Control body exceeds 4096 bytes.")
            try:
                parameters = json.loads(body)
            except (ValueError, UnicodeError):
                raise LifecycleError("HTTP_CONTROL_INVALID", "Control body must contain valid JSON.") from None
            required = {"generation", "lease_epoch"}
            if action == "drain":
                required.add("lease_seconds")
            if not isinstance(parameters, dict) or set(parameters) != required:
                raise LifecycleError("HTTP_CONTROL_INVALID", "Control body fields do not match the protocol.")
        if parameters["generation"] != self.identity["generation"]:
            raise LifecycleError("HTTP_GENERATION_CONFLICT", "Frontend generation changed.")
        return parameters

    async def _handle(self, request, *, action):
        try:
            self._authenticate(request)
            parameters = await self._parameters(request, action)
            if action == "status":
                result = self.lifecycle.status()
            elif action == "drain":
                result = self.lifecycle.drain(parameters["lease_epoch"], parameters["lease_seconds"])
            elif action == "resume":
                result = self.lifecycle.resume(parameters["lease_epoch"])
            else:
                result = self.lifecycle.commit_stop(parameters["lease_epoch"], self.on_stop)
            return JSONResponse({**self.identity, **result, "status": "SUCCESS"})
        except LifecycleError as exc:
            status = (403 if exc.code in {"HTTP_CONTROL_AUTH", "HTTP_CONTROL_ORIGIN"}
                      else 400 if exc.code == "HTTP_CONTROL_INVALID" else 409)
            return JSONResponse({**self.identity, "status": "ERROR", "error_code": exc.code,
                                 "error": str(exc)}, status_code=status)
