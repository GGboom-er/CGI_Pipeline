"""Focused Windows integration tests for bin/manage_mcp_http.ps1."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TextIO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGER = PROJECT_ROOT / "bin" / "manage_mcp_http.ps1"
SERVER_PATH = PROJECT_ROOT / "src" / "cgi_pipeline" / "server" / "server.py"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_DIR = PROJECT_ROOT / "logs"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _manager_python() -> Path:
    candidates = [
        str(PROJECT_ROOT.parents[3] / ".conda_envs" / "brain" / "python.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise RuntimeError("No Python executable available for the HTTP manager test")


class McpHttpManagerTests(unittest.TestCase):
    reserved_ports: set[int] = set()

    @classmethod
    def setUpClass(cls) -> None:
        if os.name != "nt" or not POWERSHELL:
            raise unittest.SkipTest("manage_mcp_http.ps1 is Windows-only")
        cls.python = _manager_python()

    def setUp(self) -> None:
        self.ports: set[int] = set()
        self.children: list[subprocess.Popen[str]] = []
        self.runtime_entries: list[Path] = []
        self.command_output = tempfile.TemporaryDirectory(prefix="mcp_http_manager_test_")
        self.command_index = 0

    def tearDown(self) -> None:
        for port in self.ports:
            try:
                self._run_manager("stop", port, timeout=20)
            except Exception:
                pass
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()
        for port in self.ports:
            for path in (
                RUNTIME_DIR / f"mcp_http_{port}.pid",
                LOG_DIR / f"mcp_http_{port}.stdout.log",
                LOG_DIR / f"mcp_http_{port}.stderr.log",
            ):
                path.unlink(missing_ok=True)
        for path in self.runtime_entries:
            path.unlink(missing_ok=True)
        self.command_output.cleanup()

    def _free_port(self) -> int:
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            if port not in self.reserved_ports and port != 8000:
                self.reserved_ports.add(port)
                self.ports.add(port)
                return port

    def _manager_command(self, action: str, port: int) -> list[str]:
        return [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(MANAGER),
            action,
            "-Port",
            str(port),
            "-PythonPath",
            str(self.python),
            "-StartupTimeoutSeconds",
            "30",
        ]

    @staticmethod
    def _parse_payload(stdout: str) -> dict[str, object]:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise AssertionError("HTTP manager returned no JSON output")
        if len(lines) != 1:
            raise AssertionError(f"HTTP manager returned non-JSON noise: {lines!r}")
        return json.loads(lines[0])

    def _run_manager(
        self, action: str, port: int, *, timeout: int = 50
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        invocation = self._start_manager_process(action, port)
        returncode, payload, stdout, stderr = self._collect_manager_process(
            invocation, timeout=timeout
        )
        completed = subprocess.CompletedProcess(
            self._manager_command(action, port), returncode, stdout, stderr
        )
        return completed, payload

    def _start_manager_process(
        self, action: str, port: int
    ) -> tuple[subprocess.Popen[str], TextIO, TextIO]:
        self.command_index += 1
        output_root = Path(self.command_output.name)
        stdout_file = (output_root / f"{self.command_index}.stdout").open(
            "w+", encoding="utf-8", errors="replace"
        )
        stderr_file = (output_root / f"{self.command_index}.stderr").open(
            "w+", encoding="utf-8", errors="replace"
        )
        process = subprocess.Popen(
            self._manager_command(action, port),
            cwd=PROJECT_ROOT,
            stdout=stdout_file,
            stderr=stderr_file,
            creationflags=CREATE_NO_WINDOW,
        )
        return process, stdout_file, stderr_file

    def _collect_manager_process(
        self,
        invocation: tuple[subprocess.Popen[str], TextIO, TextIO],
        timeout: int = 60,
    ) -> tuple[int, dict[str, object], str, str]:
        process, stdout_file, stderr_file = invocation
        try:
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise
            stdout_file.flush()
            stderr_file.flush()
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read()
            stderr = stderr_file.read()
            return returncode, self._parse_payload(stdout), stdout, stderr
        finally:
            stdout_file.close()
            stderr_file.close()

    def _listener_pid(self, port: int) -> int | None:
        command = (
            f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen "
            "-ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if($c){$c.OwningProcess}"
        )
        completed = subprocess.run(
            [str(POWERSHELL), "-NoLogo", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        value = completed.stdout.strip()
        return int(value) if value else None

    def _creation_time(self, process_id: int) -> str:
        command = (
            f"(Get-Process -Id {process_id}).StartTime.ToUniversalTime()"
            ".ToString('o',[Globalization.CultureInfo]::InvariantCulture)"
        )
        completed = subprocess.run(
            [str(POWERSHELL), "-NoLogo", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return completed.stdout.strip()

    def _command_line(self, process_id: int) -> str:
        command = (
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={process_id}')"
            ".CommandLine"
        )
        completed = subprocess.run(
            [str(POWERSHELL), "-NoLogo", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return completed.stdout.strip()

    def _pid_file(self, port: int) -> Path:
        return RUNTIME_DIR / f"mcp_http_{port}.pid"

    def _write_metadata(self, port: int, metadata: dict[str, object]) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        self._pid_file(port).write_text(json.dumps(metadata), encoding="utf-8")

    def _assert_process_alive(self, process_id: int) -> None:
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-Command",
                f"if(Get-Process -Id {process_id} -ErrorAction SilentlyContinue){{exit 0}}else{{exit 1}}",
            ],
            creationflags=CREATE_NO_WINDOW,
        )
        self.assertEqual(completed.returncode, 0, f"process {process_id} was stopped")

    def test_stale_dead_pid_metadata_is_removed_without_stop(self) -> None:
        port = self._free_port()
        self._write_metadata(
            port,
            {
                "pid": 2147483647,
                "creation_time": "2000-01-01T00:00:00.0000000Z",
                "port": port,
                "server_path": str(SERVER_PATH),
            },
        )

        completed, payload = self._run_manager("stop", port)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "STOPPED")
        self.assertFalse(payload["managed"])
        self.assertFalse(self._pid_file(port).exists())
        self.assertIsNone(self._listener_pid(port))

    def test_mismatched_identity_never_stops_unrelated_listener(self) -> None:
        port = self._free_port()
        code = (
            "import socket,time; "
            "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
            f"s.bind(('127.0.0.1',{port})); s.listen(); print('ready',flush=True); "
            "time.sleep(60)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        self.children.append(child)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        self._write_metadata(
            port,
            {
                "pid": child.pid,
                "creation_time": self._creation_time(child.pid),
                "port": port,
                "server_path": str(SERVER_PATH),
            },
        )

        completed, payload = self._run_manager("stop", port)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(payload["status"], "ERROR")
        self.assertIn("Refusing to stop unmanaged listener", str(payload["error"]))
        self.assertIsNone(child.poll())
        self.assertEqual(self._listener_pid(port), child.pid)
        self.assertFalse(self._pid_file(port).exists())

    def test_metadata_for_other_cgi_port_never_stops_that_service(self) -> None:
        source_port = self._free_port()
        stale_port = self._free_port()
        started, start_payload = self._run_manager("start", source_port)
        self.assertEqual(started.returncode, 0, started.stderr)
        source_pid = int(start_payload["pid"])
        source_metadata = json.loads(self._pid_file(source_port).read_text("utf-8"))
        stale_metadata = dict(source_metadata)
        stale_metadata["port"] = stale_port
        self._write_metadata(stale_port, stale_metadata)

        stopped, stop_payload = self._run_manager("stop", stale_port)

        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(stop_payload["status"], "STOPPED")
        self.assertFalse(stop_payload["managed"])
        self._assert_process_alive(source_pid)
        self.assertEqual(self._listener_pid(source_port), source_pid)
        status, status_payload = self._run_manager("status", source_port)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(status_payload["managed"])
        self.assertFalse(self._pid_file(stale_port).exists())

        reused_pid_metadata = dict(source_metadata)
        reused_pid_metadata["creation_time"] = "2000-01-01T00:00:00.0000000Z"
        self._write_metadata(source_port, reused_pid_metadata)
        refused, refused_payload = self._run_manager("stop", source_port)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("metadata_creation_time_mismatch", str(refused_payload["error"]))
        self._assert_process_alive(source_pid)
        self.assertEqual(self._listener_pid(source_port), source_pid)

        self._write_metadata(source_port, source_metadata)
        stopped_source, stopped_source_payload = self._run_manager(
            "stop", source_port
        )
        self.assertEqual(stopped_source.returncode, 0, stopped_source.stderr)
        self.assertTrue(stopped_source_payload["managed"])
        self.assertEqual(stopped_source_payload["shutdown_cleanup"], "COMPLETED")

    def test_restart_replaces_verified_repository_entry_after_relocation(self) -> None:
        port = self._free_port()
        relocated_entry = RUNTIME_DIR / f"relocated_http_entry_{port}.py"
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        relocated_entry.write_text(
            "import socket,time\n"
            "sock=socket.socket()\n"
            "sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
            f"sock.bind(('127.0.0.1',{port}))\n"
            "sock.listen()\n"
            "print('ready',flush=True)\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        self.runtime_entries.append(relocated_entry)
        child = subprocess.Popen(
            [str(self.python), "-s", str(relocated_entry), "--http"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        self.children.append(child)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        self._write_metadata(
            port,
            {
                "pid": child.pid,
                "creation_time": self._creation_time(child.pid),
                "port": port,
                "server_path": str(relocated_entry),
            },
        )

        completed, payload = self._run_manager("restart", port, timeout=60)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(payload["status"], "RUNNING")
        self.assertTrue(payload["managed"])
        self.assertTrue(payload["replaced_repository_entry"])
        self.assertEqual(Path(payload["previous_server_path"]), relocated_entry)
        self.assertIsNotNone(child.poll())
        self.assertNotEqual(int(payload["pid"]), child.pid)
        metadata = json.loads(self._pid_file(port).read_text("utf-8"))
        self.assertEqual(Path(metadata["server_path"]), SERVER_PATH)
        self.assertEqual(self._listener_pid(port), int(payload["pid"]))

    def test_concurrent_starts_and_stops_are_serialized(self) -> None:
        port = self._free_port()
        starters = [self._start_manager_process("start", port) for _ in range(4)]
        start_results = [self._collect_manager_process(process) for process in starters]

        for returncode, payload, _, stderr in start_results:
            self.assertEqual(returncode, 0, stderr)
            self.assertIn(payload["status"], {"RUNNING", "ALREADY_RUNNING"})
        start_payloads = [payload for _, payload, _, _ in start_results]
        service_pids = {int(payload["pid"]) for payload in start_payloads}
        self.assertEqual(len(service_pids), 1)
        self.assertEqual(
            sum(payload["status"] == "RUNNING" for payload in start_payloads), 1
        )
        service_pid = service_pids.pop()
        self.assertEqual(self._listener_pid(port), service_pid)
        self.assertIn(" -s ", f" {self._command_line(service_pid)} ")
        metadata = json.loads(self._pid_file(port).read_text("utf-8"))
        self.assertEqual(
            set(metadata), {"pid", "creation_time", "port", "server_path"}
        )
        self.assertEqual(metadata["pid"], service_pid)
        self.assertEqual(metadata["port"], port)
        self.assertEqual(Path(metadata["server_path"]), SERVER_PATH)

        stoppers = [self._start_manager_process("stop", port) for _ in range(4)]
        stop_results = [self._collect_manager_process(process, timeout=30) for process in stoppers]

        for returncode, payload, _, stderr in stop_results:
            self.assertEqual(returncode, 0, stderr)
            self.assertEqual(payload["status"], "STOPPED")
        self.assertEqual(
            sum(bool(payload["managed"]) for _, payload, _, _ in stop_results), 1
        )
        managed_stop = next(
            payload for _, payload, _, _ in stop_results if payload["managed"]
        )
        self.assertEqual(managed_stop["shutdown_cleanup"], "COMPLETED")
        self.assertIsNone(self._listener_pid(port))
        self.assertFalse(self._pid_file(port).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
