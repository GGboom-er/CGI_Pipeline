"""Save a managed native Blender scene using the shared file ownership rules."""

from __future__ import annotations

from pathlib import Path
import time

from cgi_pipeline.contracts import ApiContext, make_api_receipt
from cgi_pipeline.core.path_guard import is_protected_path
from cgi_pipeline.core.scene_files import directory_lock, is_managed_path, next_version_path
from cgi_pipeline.paths import REPOSITORY_ROOT


def _receipt(context, started, params, status, *, output=None, code='', error=''):
    output = output or {}
    return make_api_receipt(
        str(context.extras.get('_api_id') or 'blender.asset.save_scene'),
        str(context.extras.get('_api_version') or '1.0.1'), status, started,
        input_data={'params': dict(params)}, output=output, error_code=code, error=error,
        recovery_hint=('Inspect the actual file and current scene state before retrying; '
                       'release performs its own fresh dirty check.' if status != 'SUCCESS' else ''),
        artifacts=[output['output_path']] if output.get('output_path') else [],
    )


def _bpy(context):
    module = context.extras.get('bpy_module') or (context.extras.get('host_modules') or {}).get('bpy')
    if module is None:
        import bpy as module
    return module


def _scene_state(bpy):
    native_dirty = bool(bpy.data.is_dirty)
    actual_mode = getattr(getattr(bpy, 'app', None), 'background', None)
    background = actual_mode if type(actual_mode) is bool else None
    return {'scene': str(bpy.data.filepath or ''), 'is_saved': bool(bpy.data.is_saved),
            'dirty': native_dirty, 'native_dirty': native_dirty,
            'background': background,
            'dirty_basis': ('background_mode_unknown' if background is None else
                            'native_notifier_unconsumed' if background else 'native'),
            'clean_state': 'UNKNOWN' if background is not False else 'DIRTY' if native_dirty else 'CLEAN'}


def _blocked(path):
    return is_protected_path(str(path)) or not is_managed_path(path, REPOSITORY_ROOT)


def execute(params: dict, context: ApiContext) -> dict:
    """Use current named path or explicit target; existing targets get a new version."""
    started = time.perf_counter()
    try:
        bpy = _bpy(context)
        current = str(bpy.data.filepath or '')
        requested = str(params.get('save_path') or '').strip()
        if not requested and not current:
            raise ValueError('Unnamed scene requires an explicit absolute save_path.')
        target = Path(requested or current)
        if not target.is_absolute():
            if not current or not Path(current).is_absolute():
                raise ValueError('Relative save_path requires a current named absolute scene path.')
            target = Path(current).parent / target
        if target.suffix.lower() != '.blend':
            raise ValueError('Only native .blend scene files can be saved.')
        target = target.resolve()
        blocked = current if current and _blocked(current) else target if _blocked(target) else None
        if blocked is not None:
            return _receipt(context, started, params, 'BLOCKED',
                            output={'blocked_target': str(blocked)}, code='BLENDER_SAVE_PATH_BLOCKED',
                            error='Current scene and target must be unprotected CGI projects/runs paths.')
        target.parent.mkdir(parents=True, exist_ok=True)
        with directory_lock(target.parent, timeout=10):
            final = Path(next_version_path(str(target)))
            if _blocked(final):
                return _receipt(context, started, params, 'BLOCKED',
                                output={'blocked_target': str(final)}, code='BLENDER_SAVE_PATH_BLOCKED',
                                error='Allocated version is protected or outside managed roots.')
            # Native file saving has no bpy.data equivalent; execute synchronously
            # on the host main thread. copy=False updates the current scene path.
            result = bpy.ops.wm.save_as_mainfile(filepath=final.as_posix(), check_existing=False, copy=False)
            if 'FINISHED' not in result:
                return _receipt(context, started, params, 'ERROR', code='BLENDER_SAVE_NOT_FINISHED',
                                error=f'Blender save did not finish: {sorted(result)}')
            state = _scene_state(bpy)
            size = final.stat().st_size if final.is_file() else 0
            output = dict(state, file_size_bytes=size)
            if final.is_file():
                output['output_path'] = final.as_posix()
            if not size or not state['is_saved'] or not state['scene'] or Path(state['scene']).resolve() != final:
                return _receipt(context, started, params, 'ERROR', output=output,
                                code='BLENDER_SAVE_VERIFICATION_FAILED',
                                error='FINISHED requires a non-empty file and the matching current saved scene.')
            output.update(save_completed=True, artifact_verified=True)
            # Background Blender does not consume the window-manager save
            # notifier. Preserve its raw flag; a verified save is not a clean
            # scene claim or authorization to discard this resident host.
            if state['dirty'] and state['clean_state'] != 'UNKNOWN':
                return _receipt(context, started, params, 'NEEDS_ATTENTION', output=output,
                                code='BLENDER_SAVE_DIRTY', error='File was saved but current scene is still dirty.')
        return _receipt(context, started, params, 'SUCCESS', output=output)
    except (ImportError, OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
        return _receipt(context, started, params, 'ERROR', code='BLENDER_SAVE_OPERATION_FAILED', error=str(exc))
