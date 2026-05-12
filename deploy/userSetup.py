# userSetup.py
# CGI Pipeline Auto-Discovery Port Setup
import maya.cmds as cmds
import maya.utils as mu
import socket

def _is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def start_cgi_pipeline_ports():
    """
    自动查找 7001-7010 范围内未被占用的端口并开启 commandPort。
    完美解决双开 Maya 导致的端口冲突问题。
    """
    start_port = 7001
    end_port = 7010
    allocated_port = None

    for port in range(start_port, end_port + 1):
        if not _is_port_in_use(port):
            try:
                # 检查该端口是否已经在 Maya 内注册过
                if not cmds.commandPort(f":{port}", query=True):
                    cmds.commandPort(name=f":{port}", sourceType="python", echoOutput=True)
                allocated_port = port
                print(f"[CGI Pipeline] Foreground Port successfully bound to {port}.")
                break
            except Exception as e:
                print(f"[CGI Pipeline] Failed to bind port {port}: {e}")
                continue

    if not allocated_port:
        print("[CGI Pipeline WARNING] No available ports found in range 7001-7010. Foreground MCP disabled.")

# 使用 executeDeferred 确保在 Maya UI 初始化完成后启动端口
mu.executeDeferred(start_cgi_pipeline_ports)
