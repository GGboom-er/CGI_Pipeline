# verify_filelock.py — 验证 AssetResolver 并发版本安全
import sys, os, shutil
sys.path.insert(0, r'y:\GGbommer\scripts\CGI_Pipeline')
os.chdir(r'y:\GGbommer\scripts\CGI_Pipeline')
os.environ['PROJECT_ROOT'] = r'y:\GGbommer\scripts\CGI_Pipeline'

from core.config_loader import load_project_config
from core.asset_resolver import AssetResolver
from pathlib import Path
import threading

cfg = load_project_config('ysj')
resolver = AssetResolver(cfg)
results = []

def worker():
    ver, path = resolver.resolve_next_publish('chr', 'filelock_test', 'rig')
    results.append(ver)

# 10 个线程并发
threads = [threading.Thread(target=worker) for _ in range(10)]
[t.start() for t in threads]
[t.join() for t in threads]

print('Versions:', sorted(results))
assert len(results) == len(set(results)), 'FAIL: duplicate versions!'
print('PASS: all versions unique')

# 清理
test_dir = resolver.ai_publish_root / 'chr' / 'filelock_test'
if test_dir.exists():
    shutil.rmtree(test_dir)
