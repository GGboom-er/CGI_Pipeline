import time, json, sys
sys.path.insert(0, '.')
from mcp_server.internals import _read_audit

task_id = sys.argv[1] if len(sys.argv) > 1 else 'chain-7c7dc6e1'
print(f"Monitoring {task_id}...")
while True:
    res = _read_audit(task_id)
    s = res.get('status', '?')
    ts = time.strftime("%H:%M:%S")
    print(f"  [{ts}] {s}")
    if s in ('CHAIN_SUCCESS', 'CHAIN_ABORTED', 'ERROR', 'SUCCESS'):
        print(json.dumps(res, indent=2, ensure_ascii=False))
        break
    time.sleep(10)
