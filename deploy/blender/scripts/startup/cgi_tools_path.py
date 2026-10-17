"""Expose the shared CGI SDK and start its foreground bridge in GUI sessions."""
from pathlib import Path
import importlib.util
import sys

_SOURCE = str(Path(__file__).resolve().parents[4] / "src")


def register():
    if not Path(_SOURCE).is_dir():
        raise RuntimeError("Shared CGI source is unavailable: " + _SOURCE)
    if _SOURCE not in sys.path:
        sys.path.append(_SOURCE)
    spec = importlib.util.find_spec('cgi_pipeline')
    if not spec or not spec.origin or not Path(spec.origin).resolve().is_relative_to(Path(_SOURCE)):
        raise RuntimeError('Another CGI SDK shadows the shared source')
    print('CGI tools path ready: ' + spec.origin, flush=True)
    import bpy
    if not bpy.app.background:
        from cgi_pipeline.hosts.blender import native_bridge
        session = native_bridge.start()
        print(f"CGI Blender foreground ready on port {session['port']}", flush=True)


def unregister():
    import bpy
    if not bpy.app.background:
        from cgi_pipeline.hosts.blender import native_bridge
        native_bridge.stop()
    # Imported modules may still depend on this path. Restart after uninstall.
