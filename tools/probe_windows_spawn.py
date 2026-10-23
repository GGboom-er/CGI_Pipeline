"""Bounded diagnostic for Windows shared-process environment inheritance."""
import os
from pathlib import Path
import sys
import tempfile
import time
from cgi_pipeline.core import service_manager as sm

root = Path(tempfile.mkdtemp(prefix='cgi-wmi-probe-'))
output = root / 'result.txt'
code = f'''import pathlib, sys, os, traceback
p = pathlib.Path({str(output)!r})
p.write_text(str((sys.executable, os.getcwd(), os.getenv('SMOKE_TEST'))))
try:
 import cgi_pipeline.core.tasks
 p.write_text(p.read_text() + '\\nimport PASS')
except BaseException:
 p.write_text(p.read_text() + '\\n' + traceback.format_exc())
'''
env = sm._managed_python_env()
env['SMOKE_TEST'] = 'yes'
def in_restrictive_job(*args, **kwargs):
    error = PermissionError('test restrictive Job')
    error.winerror = 5
    raise error
sm.subprocess.Popen = in_restrictive_job
process = sm._spawn_shared_process([sys.executable, '-c', code], cwd=root, env=env)
time.sleep(5)
print(root, process.pid, output.exists())
print(output.read_text() if output.exists() else process.poll())
