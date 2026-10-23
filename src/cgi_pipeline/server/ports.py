# cgi_pipeline/server/ports.py
# ── 共享：Maya commandPort 端口扫描 ──
# 单一真相源，消除 tools_readonly / tools_operations / foreground_client 三处重复实现。
# 叶子模块（只依赖 os/socket），任何 mcp_server 模块可安全 import，无循环依赖。

import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor


def maya_port_range() -> range:
    """Maya commandPort 扫描范围，默认覆盖 7001-7020（可经 env 覆盖）。"""
    start = int(os.getenv('MAYA_FOREGROUND_PORT_START', '7001'))
    end = int(os.getenv('MAYA_FOREGROUND_PORT_END', '7020'))
    if end < start:
        start, end = end, start
    return range(start, end + 1)


def maya_scan_ports() -> list[int]:
    """Return the bounded scan range plus explicitly registered Maya ports."""
    ports = set(maya_port_range())
    raw = os.getenv('MAYA_FOREGROUND_PORTS', '')
    for token in filter(None, re.split(r'[\s,;]+', raw.strip())):
        try:
            port = int(token)
        except ValueError as exc:
            raise ValueError(f'invalid MAYA_FOREGROUND_PORTS entry: {token!r}') from exc
        if port < 1 or port > 65535:
            raise ValueError(f'Maya foreground port must be between 1 and 65535: {port}')
        ports.add(port)
    return sorted(ports)


def _probe_maya_port(port: int, timeout: float) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            return connection.connect_ex(('127.0.0.1', port)) == 0
    except OSError:
        return False


def discover_maya_ports(timeout: float = 0.2, max_workers: int = 20) -> list[int]:
    """并行扫描本机 Maya commandPort，并按端口号稳定返回结果。"""
    if timeout <= 0:
        raise ValueError('timeout must be greater than zero')
    if max_workers <= 0:
        raise ValueError('max_workers must be greater than zero')

    ports = maya_scan_ports()
    if not ports:
        return []

    worker_count = min(len(ports), max_workers)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = executor.map(lambda port: _probe_maya_port(port, timeout), ports)
        return [port for port, is_open in zip(ports, results) if is_open]
