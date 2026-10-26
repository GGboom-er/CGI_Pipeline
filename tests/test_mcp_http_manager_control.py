"""Isolated PowerShell manager protocol checks; no CGI service or DCC starts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin" / "mcp_http_control.ps1"
MANAGER = ROOT / "bin" / "manage_mcp_http.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class HttpManagerControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.name != "nt" or not POWERSHELL:
            raise unittest.SkipTest("PowerShell process control is Windows-only")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="http_control_isolated_")
        self.directory = Path(self.temp.name).resolve()  # long form: Resolve-Path expands 8.3 names
        self.control = self.directory / "control.json"
        self.control.write_text(json.dumps({
            "protocol_version": 1, "pid": 424242,
            "process_start_time": 1767225600.0,
            "generation": "21d1fd70-c47d-4926-99c8-d58f245e8f2f",
            "secret": "test-secret-" * 4,
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, body: str, setup: str = "", native_workspace_stub: bool = True) -> dict:
        # The isolated directory stands in for the repository root, so receipts live under its
        # projects/ tree. Native workspace identity is stubbed except in the test that covers it.
        workspace_stub = ("function Confirm-InitialCutoverWorkspace {param($ReceiptPath,$ProjectsRoot)}"
                          if native_workspace_stub else "")
        script = f"""
$ErrorActionPreference='Stop'
. {_quote(HELPER)}
$ControlFile={_quote(self.control)}
$ProjectRoot={_quote(self.directory)}
$ListenAddress='127.0.0.1'; $Port=49999
$DrainTimeoutSeconds=1; $ShutdownTimeoutSeconds=1
$script:calls=[Collections.Generic.List[string]]::new()
$script:epoch=0L; $script:drains=0; $script:remaining=@(0)
$script:exitResult=$true; $script:cleanupResult='COMPLETED'
$script:failStop=$false; $script:wrongGeneration=$false
$fakeProcess=[pscustomobject]@{{Id=424242;StartTime=[datetime]'2026-01-01T00:00:00Z';HasExited=$false}}
$fakeProcess | Add-Member ScriptMethod WaitForExit {{param($ms) $script:calls.Add('wait');return $script:exitResult}}
$script:identity=[pscustomobject]@{{valid=$true;process=$fakeProcess;managed_server_path='fixture.py'}}
function Get-ManagedIdentity {{param([switch]$RequireListener,[switch]$AllowRepositoryRelocation) return $script:identity}}
function Resolve-PythonPath {{return {_quote(ROOT.parents[3] / '.conda_envs' / 'brain' / 'python.exe')}}}
$env:PYTHONPATH={_quote(ROOT / 'src')};$env:PYTHONDONTWRITEBYTECODE='1'
function ConvertTo-UtcTicks {{param($Value)
    if($Value -is [datetime]){{return $Value.ToUniversalTime().Ticks}}
    return ([DateTimeOffset]::Parse([string]$Value)).UtcDateTime.Ticks
}}
function Stop-Process {{param($InputObject,[switch]$Force)
    if(Test-Path -LiteralPath $InitialProtocolCutoverReceipt){{throw 'receipt was not consumed first'}}
    $script:calls.Add('initial-force')
}}
function Invoke-CooperativeShutdown {{$script:calls.Add('preserve'); return [pscustomobject]@{{status=$script:cleanupResult;detail='fixture'}}}}
function Invoke-RestMethod {{
    param($Uri,$Headers,$TimeoutSec,$MaximumRedirection,$ErrorAction,$Method,$ContentType,$Body)
    if($Headers['X-CGI-Control-Token'] -cne ('test-secret-'*4)){{throw 'wrong token'}}
    $operation=([uri]$Uri).AbsolutePath.Split('/')[-1]; $script:calls.Add($operation)
    $request=if($Body){{$Body|ConvertFrom-Json}}else{{$null}}
    $state='accepting';$count=0
    if($operation -eq 'drain'){{
        if($script:epoch -eq 0){{$script:epoch=1}}
        $state='draining';$count=$script:remaining[[math]::Min($script:drains,$script:remaining.Count-1)]
        $script:drains++
    }}
    if($operation -eq 'resume'){{$script:epoch++;$state='accepting'}}
    if($operation -eq 'stop'){{if($script:failStop){{throw 'fixture stop unavailable'}};$state='stopping'}}
    $control=Get-Content -Raw -LiteralPath $ControlFile|ConvertFrom-Json
    return [pscustomobject]@{{protocol_version=1;pid=424242;process_start_time=1767225600.0;
        generation=$(if($script:wrongGeneration){{'wrong'}}else{{$control.generation}});
        state=$state;active_count=$count;lease_epoch=$script:epoch;lease_remaining_seconds=60}}
}}
{workspace_stub}
{setup}
try {{ {body}; $errorText=$null }} catch {{ $errorText=$_.Exception.Message }}
@{{error=$errorText;calls=@($script:calls.ToArray());drains=$script:drains}}|ConvertTo-Json -Compress
"""
        path = self.directory / "case.ps1"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path)],
            text=True, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("test-secret-", result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_idle_stop_uses_atomic_commit_then_normal_exit(self) -> None:
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)")
        self.assertIsNone(result["error"])
        self.assertEqual(result["calls"], ["status", "drain", "preserve", "stop", "wait"])

    def test_active_work_is_polled_before_preserve_and_commit(self) -> None:
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)",
                           "$script:remaining=@(2,1,0);$DrainTimeoutSeconds=3")
        self.assertIsNone(result["error"])
        self.assertEqual(result["calls"], ["status", "drain", "drain", "drain", "preserve", "stop", "wait"])

    def test_drain_timeout_resumes_existing_service(self) -> None:
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)", "$script:remaining=@(1)")
        self.assertIn("drain timed out", result["error"])
        self.assertEqual(result["calls"][-1], "resume")
        self.assertNotIn("stop", result["calls"])
        self.assertNotIn("preserve", result["calls"])

    def test_worker_preservation_failure_resumes_without_stop(self) -> None:
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)", "$script:cleanupResult='FAILED'")
        self.assertIn("Worker preservation failed", result["error"])
        self.assertEqual(result["calls"], ["status", "drain", "preserve", "resume"])

    def test_stop_transport_error_attempts_resume_without_process_wait(self) -> None:
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)", "$script:failStop=$true")
        self.assertIn("stop request failed", result["error"])
        self.assertEqual(result["calls"], ["status", "drain", "preserve", "stop", "resume"])

    def test_committed_exit_timeout_never_resumes_or_forces(self) -> None:
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)", "$script:exitResult=$false")
        self.assertIn("no forced termination", result["error"])
        self.assertEqual(result["calls"], ["status", "drain", "preserve", "stop", "wait"])

    def test_wrong_response_generation_cannot_drain(self) -> None:
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)", "$script:wrongGeneration=$true")
        self.assertIn("identity mismatch", result["error"])
        self.assertEqual(result["calls"], ["status"])

    def test_missing_protocol_metadata_refuses_without_http_or_process_action(self) -> None:
        self.control.unlink()
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)")
        self.assertIn("requires protocol-v1", result["error"])
        self.assertEqual(result["calls"], [])

    def test_invalid_protocol_identity_refuses_without_http(self) -> None:
        data = json.loads(self.control.read_text("utf-8"))
        data["pid"] = 424243
        self.control.write_text(json.dumps(data), encoding="utf-8")
        result = self._run("[void](Stop-HttpServiceCooperatively $script:identity)")
        self.assertIn("invalid or does not identify", result["error"])
        self.assertEqual(result["calls"], [])

    def test_normal_shutdown_function_contains_no_force_or_pid_only_kill(self) -> None:
        source = HELPER.read_text("utf-8").split("function Stop-HttpServiceForInitialProtocolCutover", 1)[0]
        self.assertNotIn("Stop-Process", source)
        self.assertNotIn(".Kill(", source)
        self.assertIn("Stop-HttpServiceCooperatively $confirmed", MANAGER.read_text("utf-8"))

    def _cutover_receipt(self, **changes: object) -> Path:
        now = datetime.now(timezone.utc)
        data = {
            "schema_version": 1, "kind": "cgi_http_initial_protocol_cutover",
            "port": 49999, "pid": 424242, "creation_time": "2026-01-01T00:00:00Z",
            "server_path": "fixture.py", "approved": True,
            "approved_at_utc": now.isoformat(),
            "expires_at_utc": (now + timedelta(minutes=5)).isoformat(),
            "coordination": [
                {"thread_id": "one", "title": "first", "ack": True, "evidence": "paused"},
                {"thread_id": "two", "title": "second", "ack": True, "evidence": "paused"},
            ],
        }
        data.update(changes)
        path = self.directory / "projects" / "default" / "fixture_ws" / "cutover.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_explicit_initial_cutover_consumes_receipt_before_verified_force(self) -> None:
        receipt = self._cutover_receipt()
        self.control.unlink()
        result = self._run("[void](Stop-HttpServiceForInitialProtocolCutover $script:identity)",
                           f"$InitialProtocolCutoverReceipt={_quote(receipt)}")
        self.assertIsNone(result["error"])
        self.assertEqual(result["calls"], ["preserve", "initial-force", "wait"])
        self.assertFalse(receipt.exists())
        self.assertTrue(Path(str(receipt) + ".consumed").exists())

    def test_initial_cutover_never_falls_back_when_protocol_metadata_exists(self) -> None:
        receipt = self._cutover_receipt()
        result = self._run("[void](Stop-HttpServiceForInitialProtocolCutover $script:identity)",
                           f"$InitialProtocolCutoverReceipt={_quote(receipt)}")
        self.assertIn("forbidden when control metadata exists", result["error"])
        self.assertEqual(result["calls"], [])
        self.assertTrue(receipt.exists())

    def test_initial_cutover_rejects_stale_or_unacknowledged_approval(self) -> None:
        for changes in (
            {"pid": 424243}, {"creation_time": "2000-01-01T00:00:00Z"},
            {"expires_at_utc": "2000-01-01T00:00:00Z"},
            {"coordination": [{"thread_id": "one", "title": "first", "ack": False, "evidence": "pending"}]},
        ):
            with self.subTest(changes=changes):
                receipt = self._cutover_receipt(**changes)
                self.control.unlink(missing_ok=True)
                result = self._run("[void](Stop-HttpServiceForInitialProtocolCutover $script:identity)",
                                   f"$InitialProtocolCutoverReceipt={_quote(receipt)}")
                self.assertIn("invalid, expired", result["error"])
                self.assertEqual(result["calls"], [])
                self.assertTrue(receipt.exists())

    def test_initial_cutover_worker_preservation_failure_does_not_consume(self) -> None:
        receipt = self._cutover_receipt()
        self.control.unlink()
        result = self._run("[void](Stop-HttpServiceForInitialProtocolCutover $script:identity)",
                           f"$InitialProtocolCutoverReceipt={_quote(receipt)};$script:cleanupResult='FAILED'")
        self.assertIn("Worker preservation failed", result["error"])
        self.assertEqual(result["calls"], ["preserve"])
        self.assertTrue(receipt.exists())

    def test_initial_cutover_rejects_receipt_outside_managed_projects(self) -> None:
        self.control.unlink()
        result = self._run("[void](Stop-HttpServiceForInitialProtocolCutover $script:identity)",
                           f"$InitialProtocolCutoverReceipt={_quote(MANAGER)}")
        self.assertIn("inside a managed projects workspace", result["error"])
        self.assertEqual(result["calls"], [])

    def test_initial_cutover_rejects_reparse_receipt_before_approval_or_force(self) -> None:
        receipt = self._cutover_receipt()
        self.control.unlink()
        setup = f"""$InitialProtocolCutoverReceipt={_quote(receipt)}
function Get-Item {{param($LiteralPath,[switch]$Force)
    return [pscustomobject]@{{Attributes=[IO.FileAttributes]::ReparsePoint;FullName=$LiteralPath}}
}}"""
        result = self._run("[void](Stop-HttpServiceForInitialProtocolCutover $script:identity)", setup)
        self.assertIn("reparse points", result["error"])
        self.assertEqual(result["calls"], [])

    def test_initial_cutover_rechecks_identity_after_worker_preservation(self) -> None:
        receipt = self._cutover_receipt()
        self.control.unlink()
        setup = f"""$InitialProtocolCutoverReceipt={_quote(receipt)}
function Invoke-CooperativeShutdown {{
    $script:calls.Add('preserve');$script:identity=[pscustomobject]@{{valid=$false;process=$null}}
    return [pscustomobject]@{{status='COMPLETED'}}
}}"""
        result = self._run("[void](Stop-HttpServiceForInitialProtocolCutover $script:identity)", setup)
        self.assertIn("identity or protocol changed", result["error"])
        self.assertEqual(result["calls"], ["preserve"])
        self.assertTrue(receipt.exists())

    def test_cutover_workspace_uses_existing_native_identity_validation(self) -> None:
        result = self._run(
            f"Confirm-InitialCutoverWorkspace {_quote(self.control)} {_quote(ROOT / 'projects' / 'ysj')}",
            native_workspace_stub=False,
        )
        self.assertIn("not inside a valid native CGI workspace", result["error"])
        self.assertEqual(result["calls"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
