import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from dccs.maya.worker import MayaCommandPortWorker

def run_install():
    worker = MayaCommandPortWorker(port=7002)
    try:
        worker.start()
    except Exception as e:
        print(f"无法连接: {e}")
        return
        
    code = """
import sys
import subprocess
result = {'status': 'SUCCESS'}
try:
    print("正在为您的 Maya 环境安装 pycpd 和 scipy，请稍候...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pycpd", "scipy"])
    result['message'] = "✅ pycpd 安装成功！"
    print(result['message'])
except Exception as e:
    import traceback
    result['status'] = 'ERROR'
    result['error'] = traceback.format_exc()
"""

    payload = {
        'task_id': 'install_pycpd',
        'skill_id': 'exec_code',
        'parameters': {
            'code': code,
            'description': '安装 pycpd 依赖'
        }
    }
    
    try:
        print("发送 pip install 指令...")
        res = worker.run_skill(payload)
        print("返回:", res)
    finally:
        worker.shutdown()

if __name__ == "__main__":
    run_install()
