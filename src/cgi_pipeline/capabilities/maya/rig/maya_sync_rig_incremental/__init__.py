def execute(payload):
    from .maya_sync_rig_incremental import execute as _execute
    return _execute(payload)
