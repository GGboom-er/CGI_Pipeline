"""Maya commandPort 通信 — 单连接模式。"""
import socket
import time
import json
import sys


class MayaConnection:
    def __init__(self, port=7001):
        self.port = port
        self.sock = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(('127.0.0.1', self.port))

    def send(self, cmd, wait=1.0):
        """发送命令并读取响应。"""
        self.sock.sendall((cmd + '\n').encode('utf-8'))
        time.sleep(wait)
        chunks = []
        self.sock.settimeout(1.0)
        while True:
            try:
                data = self.sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
            except socket.timeout:
                break
        return b''.join(chunks).decode('utf-8', errors='replace')

    def close(self):
        if self.sock:
            self.sock.close()


if __name__ == "__main__":
    print("=== Maya Connection Test (persistent) ===\n")

    conn = MayaConnection(7001)
    try:
        conn.connect()
        print("Connected to Maya:7001")
    except ConnectionRefusedError:
        print("ERROR: Cannot connect to Maya on port 7001")
        print("Please run in Maya: cmds.commandPort(name=':7001', sourceType='python')")
        sys.exit(1)

    # 测试简单命令
    r = conn.send("1+1", wait=0.5)
    print(f"  1+1 = [{r.strip()}]")

    r = conn.send("import maya.cmds as cmds", wait=0.5)
    print(f"  import cmds: [{r.strip()}]")

    r = conn.send("cmds.ls(type='mesh')[:5]", wait=1.0)
    print(f"  first 5 meshes: [{r.strip()}]")

    # 获取完整场景信息
    scan = """
import maya.cmds as cmds
import json
_meshes = cmds.ls(type='mesh', long=True) or []
_info = {'skinned': [], 'bs': [], 'total': len(_meshes)}
for _m in _meshes:
    _xf = (cmds.listRelatives(_m, parent=True, fullPath=True) or [None])[0]
    if not _xf: continue
    _h = cmds.listHistory(_m) or []
    _sc = [x for x in _h if cmds.nodeType(x) == 'skinCluster']
    _bsn = [x for x in _h if cmds.nodeType(x) == 'blendShape']
    if _sc:
        _jts = cmds.skinCluster(_sc[0], q=True, inf=True) or []
        _vc = cmds.polyEvaluate(_xf, vertex=True)
        _info['skinned'].append({'m': _xf, 's': _sc[0], 'j': len(_jts), 'v': _vc})
    if _bsn:
        _tgts = cmds.blendShape(_bsn[0], q=True, target=True) or []
        _info['bs'].append({'m': _xf, 'b': _bsn[0], 'tc': len(_tgts), 't': _tgts[:5]})
json.dumps(_info)
"""
    r = conn.send(scan, wait=3.0)
    print(f"\n  Scene scan response length: {len(r)}")
    print(f"  Raw (first 500): {r[:500]}")

    conn.close()
