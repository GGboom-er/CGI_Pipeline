"""Thin CGI boundary over the registered standalone XGen tool package."""
import importlib
import json
from pathlib import Path
import sys
import time
from cgi_pipeline.contracts import make_api_receipt


def _module(name):
    registry = Path(__file__).resolve().parents[2] / 'data' / 'packages.json'
    packages = json.loads(registry.read_text(encoding='utf-8'))
    row = next(p for p in packages if p['package_id'] == 'xgen_cache')
    source = Path(row['source']['path']).resolve()
    if not (source / 'xgen_cache' / '__init__.py').is_file():
        raise RuntimeError('Registered xgen_cache source is unavailable: ' + str(source))
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    module = importlib.import_module('xgen_cache.' + name)
    if Path(module.__file__).resolve().parent != source / 'xgen_cache':
        raise RuntimeError('Loaded xgen_cache differs from registered source')
    return module


def execute(params, context):
    started = time.perf_counter()
    api_id = context.extras['_api_id']
    version = ('2.2.0' if api_id == 'maya.xgen.export' else
               '2.0.0' if api_id.rsplit('.', 1)[-1] in ('record', 'inspect', 'rebuild') else '1.0.0')
    try:
        operation = api_id.rsplit('.', 1)[-1]
        if operation == 'scene':
            output = _module('scene').inspect_scene()
        elif operation == 'export':
            from cgi_pipeline.core.path_guard import assert_save_allowed
            destination = Path(params['directory']).resolve()
            assert_save_allowed(str(destination))
            if context.execution_mode == 'background' and (
                    not context.run_dir or not destination.is_relative_to(Path(context.run_dir).resolve())):
                raise ValueError('Background export must stay inside the task workspace')
            output = _module('core').export(params['directory'], backend=params.get('backend', 'verified'))
        else:
            module = _module('structure')
            nodes = params.get('nodes', [])
            output = (module.rebuild(nodes, params.get('root') or None)
                      if operation == 'rebuild' else getattr(module, operation)(nodes))
        return make_api_receipt(api_id, version, 'SUCCESS', started,
                                input_data=dict(params), output=output)
    except Exception as error:
        return make_api_receipt(api_id, version, 'ERROR', started,
                                input_data=dict(params), error_code='XGEN_OPERATION_FAILED',
                                error=str(error), recovery_hint='Inspect scene and parameters; do not overwrite records or outputs.')
