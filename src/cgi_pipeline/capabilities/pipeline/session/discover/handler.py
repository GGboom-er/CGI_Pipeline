"""Discover live DCC sessions through CGI's sanctioned transports."""

from __future__ import annotations

import time

from cgi_pipeline.contracts import ApiContext, make_api_receipt


def _with_identity(dcc: str, session: dict) -> dict:
    result = {"dcc": dcc, **session}
    port = result.get("port")
    result.setdefault("session_id", f"{dcc}:{port}" if port else dcc)
    return result


def execute(params: dict, context: ApiContext) -> dict:
    """Return discovered sessions without opening or changing a DCC scene."""
    started_at = time.perf_counter()
    requested_dcc = params["dcc"]
    sessions: list[dict] = []

    if requested_dcc in {"maya", "all"}:
        from cgi_pipeline.server.ports import discover_maya_ports

        sessions.extend(
            _with_identity("maya", {"port": port})
            for port in discover_maya_ports()
        )
    if requested_dcc in {"blender", "all"}:
        from cgi_pipeline.hosts.blender.foreground_client import discover_blender_sessions

        sessions.extend(
            _with_identity("blender", session)
            for session in discover_blender_sessions()
        )
    if requested_dcc in {"ue", "all"}:
        from cgi_pipeline.hosts.ue.foreground_client import discover_ue_sessions

        sessions.extend(
            _with_identity("ue", session)
            for session in discover_ue_sessions()
        )

    selected = sessions[0] if len(sessions) == 1 else None
    status = "SUCCESS" if selected or requested_dcc == "all" else "NEEDS_ATTENTION"
    return make_api_receipt(
        str(context.extras.get("_api_id") or "pipeline.session.discover"),
        str(context.extras.get("_api_version") or "1.0.0"),
        status,
        started_at,
        input_data={"dcc": requested_dcc},
        output={"sessions": sessions, "selected_session": selected},
        error_code="NO_UNIQUE_SESSION" if status != "SUCCESS" else "",
        error=(
            f"Expected one active {requested_dcc} session, found {len(sessions)}."
            if status != "SUCCESS" else ""
        ),
        recovery_hint=(
            "Open the requested DCC, or choose one returned port explicitly in the next API call."
            if status != "SUCCESS" else ""
        ),
    )
