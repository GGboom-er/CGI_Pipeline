"""One explicit external numerical boundary; native Maya NumPy stays untouched."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from contextlib import contextmanager
import queue
import threading

OWNER = Path(__file__).resolve().parent
_PROBES = {}
_SESSION = None


@contextmanager
def numeric_session():
    """One operation-owned math process for many small independent skins."""
    global _SESSION
    if _SESSION is not None:
        yield
        return
    state = {}
    _SESSION = state
    try:
        yield
    finally:
        _SESSION = None
        process = state.get("process")
        if process:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            state["stderr"].close()


def _session_execute(command, config, cwd, environment, request):
    if "process" not in _SESSION:
        errors = (cwd / "numeric_session.log").open("w", encoding="utf-8")
        process = subprocess.Popen(command[:3] + ["--serve"], cwd=str(cwd), env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True,
            encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        replies = queue.Queue()
        def read_lines():
            for line in process.stdout:
                replies.put(line)
            replies.put(None)
        threading.Thread(target=read_lines, daemon=True).start()
        _SESSION.update(process=process, stderr=errors, replies=replies)
    process = _SESSION["process"]
    process.stdin.write(json.dumps({"request": request}) + "\n")
    process.stdin.flush()
    try:
        line = _SESSION["replies"].get(timeout=config["timeout_seconds"])
    except queue.Empty as exc:
        raise RuntimeError("numeric session timed out before Maya weights were written") from exc
    if line is None:
        raise RuntimeError("numeric session exited; inspect numeric_session.log")
    reply = json.loads(line)
    return subprocess.CompletedProcess(command, 0 if reply.get("status") == "SUCCESS" else 1,
                                       stdout=line, stderr=reply.get("error", ""))


def runtime_config():
    path = OWNER / "solver_runtime.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("python_executable", "runtime_owner"):
        if not Path(config[key]).is_file():
            raise ImportError("registered numeric runtime {} is missing: {}".format(key, config[key]))
    if not Path(config["native_bin"]).is_dir():
        raise ImportError("registered numeric runtime native_bin is missing")
    digest = hashlib.sha256(path.read_bytes())
    digest.update(str(Path(config["python_executable"]).stat().st_mtime_ns).encode())
    for name in ("dair_worker.py", "dair_kernel.py", "dair_data.py"):
        digest.update((OWNER / name).read_bytes())
    geometry_owner = OWNER.parents[1] / 'asset' / 'mesh_reduce'
    for name in ('analysis.py','analysis_worker.py','similarity.py','grouping.py'):
        digest.update((geometry_owner/name).read_bytes())
    config["fingerprint"] = digest.hexdigest()
    return config


def _execute(arguments, config, cwd):
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PATH"] = config["native_bin"] + os.pathsep + environment.get("PATH", "")
    command = [config["python_executable"], "-I", str(OWNER / "dair_worker.py"), *arguments]
    if _SESSION is not None and arguments[0] == "--request":
        return _session_execute(command, config, cwd, environment, arguments[1])
    try:
        completed = subprocess.run(command, cwd=str(cwd), env=environment, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=config["timeout_seconds"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("numeric solver exceeded its configured timeout; no Maya weights written") from exc
    return completed


def require_deps():
    """Probe a real constrained solve once per source/runtime fingerprint."""
    import numpy as np
    config = runtime_config()
    fingerprint = config["fingerprint"]
    if fingerprint not in _PROBES:
        completed = _execute(["--probe"], config, OWNER)
        if completed.returncode:
            raise ImportError("registered numerical solver probe failed: " + completed.stderr[-4000:])
        try:
            result = json.loads(completed.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ImportError("registered numerical solver returned no valid probe receipt") from exc
        _PROBES[fingerprint] = result
    return {"host_numpy": np.__version__, "numeric_worker": dict(_PROBES[fingerprint]),
            "runtime_owner": config["runtime_owner"], "runtime_fingerprint": fingerprint,
            "solver": "scipy.optimize.minimize:SLSQP"}


def _workspace_for(candidate):
    """Reuse the canonical path protection and workspace identity owner."""
    from cgi_pipeline.paths import REPOSITORY_ROOT
    from cgi_pipeline.core.path_guard import is_protected_path
    from cgi_pipeline.core.scene_files import is_managed_path
    from cgi_pipeline.core.workspace import verify_workspace_identity, WorkspaceError
    if is_protected_path(str(candidate)) or not is_managed_path(candidate, REPOSITORY_ROOT):
        raise ValueError("solver_workdir must be inside an unprotected managed CGI workspace")
    for parent in [candidate, *candidate.parents]:
        marker = parent / ".info/run_state.json"
        if not marker.is_file():
            continue
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("workspace state must be an object")
            fields = [state.get(key) for key in ("wf_id", "project", "business_task_id")]
            if not all(isinstance(value, str) and value for value in fields):
                raise ValueError("workspace identity fields are missing")
            verify_workspace_identity(parent, *fields)
        except (ValueError, OSError, WorkspaceError) as exc:
            raise ValueError("solver workspace identity is invalid: " + str(exc)) from exc
        return parent
    raise ValueError("solver_workdir requires an existing verified managed workspace")


def resolve_workdir(value=None):
    """Use an explicit managed path, or the current managed Maya scene's owner."""
    if value:
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError("solver_workdir must be an absolute managed workspace path")
        candidate = candidate.resolve()
    else:
        import maya.cmds as cmds
        scene = cmds.file(query=True, sceneName=True) or ""
        if not scene:
            raise ValueError("an unnamed scene requires solver_workdir in a managed workspace")
        scene_path = Path(scene).resolve()
        candidate = _workspace_for(scene_path) / "work" / "skin_solver"
    _workspace_for(candidate)
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def solve(points, edges, weights, masks, moments, pose_count, options):
    import numpy as np
    config = runtime_config()
    parent = resolve_workdir(options.get("solver_workdir"))
    request_id = "dair_" + uuid.uuid4().hex
    root = parent / request_id
    root.mkdir()
    request = {"request_id": request_id, "runtime_fingerprint": config["fingerprint"],
               "threshold": options["threshold"], "max_influences": options["max_influences"],
               "neighbor_rings": options.get("neighbor_rings", 1),
               "tiered": options.get("tiered", False),
               "neighbor_max_delta": options.get("neighbor_max_delta", .02)}
    np.savez(root / "input.npz", points=points, edges=edges, weights=weights,
             masks=masks, moments=moments, pose_count=np.asarray(pose_count))
    request_path = root / "request.json"
    request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    try:
        completed = _execute(["--request", str(request_path)], config, root)
    except RuntimeError as exc:
        (root / "failure.json").write_text(json.dumps({"error": str(exc)}), encoding="utf-8")
        raise RuntimeError("numeric solver failed before Maya weights were written; evidence: " + str(root)) from exc
    (root / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (root / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError("numeric solver failed before Maya weights were written; evidence: " + str(root))
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    if result["request_id"] != request_id or result["runtime_fingerprint"] != config["fingerprint"]:
        raise RuntimeError("numeric solver result identity mismatch; evidence: " + str(root))
    with np.load(root / "output.npz", allow_pickle=False) as data:
        output = data["weights"].copy()
    if output.shape != weights.shape or not np.isfinite(output).all() or np.any(output < 0):
        raise RuntimeError("numeric solver returned invalid weights; evidence: " + str(root))
    if (np.any(np.count_nonzero(output, axis=1) > options["max_influences"]) or
            not np.allclose(output.sum(axis=1), 1, atol=1e-7, rtol=0)):
        raise RuntimeError("numeric solver returned invalid support or normalization; evidence: " + str(root))
    effective = output / output.sum(axis=1)[:, None]
    if np.any((effective > 0) & (effective <= options["threshold"])):
        raise RuntimeError("numeric solver returned positive weights at or below threshold; evidence: " + str(root))
    result["metrics"]["solver_artifact_dir"] = str(root)
    return output, result["metrics"]
