# mcp_server/ports.py
# ── 共享：Maya commandPort 端口扫描 ──
# 单一真相源，消除 tools_readonly / tools_operations / foreground_client 三处重复实现。
# 叶子模块（只依赖 os/socket），任何 mcp_server 模块可安全 import，无循环依赖。

import os
import socket


def maya_port_range() -> range:
    """Maya commandPort 扫描范围，默认覆盖 7001-7020（可经 env 覆盖）。"""
    start = int(os.getenv('MAYA_FOREGROUND_PORT_START', '7001'))
    end = int(os.getenv('MAYA_FOREGROUND_PORT_END', '7020'))
    if end < start:
        start, end = end, start
    return range(start, end + 1)


def discover_maya_ports() -> list[int]:
    """扫描本机活跃的 Maya commandPort，返回端口列表。"""
    active = []
    for p in maya_port_range():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(('127.0.0.1', p)) == 0:
                    active.append(p)
        except OSError:
            pass
    return active
