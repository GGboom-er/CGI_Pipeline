# cgi_pipeline/hosts/maya/rpyc_server.py
# ── RPyC 服务端：运行在 Maya 进程内部的后台线程 ──
#
# 设计目标：
# 1. 零日志污染：客户端断连是管线的正常行为，不应在 Maya Script Editor 产生任何报错
# 2. 两层防御：Service 层 (on_disconnect) + Server 层 (_authenticate_and_serve_client)
#
# 根因分析（完整 EOFError 路径）：
# ┌─ 路径A：客户端正常使用后断开 ──────────────────────────────────────────┐
# │  conn.close() → 服务端 on_disconnect → EOFError（被 Service 层静默）  │
# └──────────────────────────────────────────────────────────────────────────┘
# ┌─ 路径B：客户端在握手阶段就断开（ping 失败后重连/Worker shutdown）──────┐
# │  connect() → 服务端 _serve_client → _install(conn, conn.root)         │
# │  → sync_request(HANDLE_GETROOT) → 客户端已断 → EOFError              │
# │  → _authenticate_and_serve_client 的 except 块打印 + raise            │
# │  → threading 模块捕获并再次打印完整 traceback（双重污染）             │
# └──────────────────────────────────────────────────────────────────────────┘
#
# 解决方案：
# Service 层：覆写 on_disconnect，吃掉路径 A
# Server 层：覆写 _authenticate_and_serve_client，吃掉路径 B（不 raise）

# ══════════════════════════════════════════════════════════════
# 依赖路径注入（必须在 import rpyc 之前执行）
#
# Maya 自带的 Python 解释器默认看不到仓库 conda env 的 site-packages。
# worker._send_bootstrap 会把 MCP server 进程的 site-packages 路径列表
# 作为常量 _CGI_INJECT_SITE_PACKAGES 替换进本文件顶部；我们在此把这些
# 路径塞进 sys.path，让 Maya 能 import 到 cgi_pipeline env 里的 rpyc。
# 这样就不用再往 mayapy 里 pip install 任何东西，换机器也不用重装。
# ══════════════════════════════════════════════════════════════
import sys
_CGI_INJECT_SITE_PACKAGES = []  # noqa: E501 ← 由 worker._send_bootstrap 运行时替换
for _p in _CGI_INJECT_SITE_PACKAGES:
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

import threading
import socket
from contextlib import closing
import rpyc
from rpyc.utils.server import ThreadedServer


class _GracefulClassicService(rpyc.classic.ClassicService):
    """
    路径 A 防御：覆写 on_disconnect，静默处理客户端正常断开。
    """
    def on_disconnect(self, conn):
        """客户端断开连接时的回调 — 管线正常行为，无需任何报错"""
        pass


class _SilentThreadedServer(ThreadedServer):
    """
    路径 B 防御：覆写 _authenticate_and_serve_client。
    
    原版 RPyC 的行为是：
        except Exception:
            self.logger.exception("client connection terminated abruptly")
            raise  ← 这个 raise 导致 threading 模块再打一次 traceback
    
    我们的版本：
    - 对 EOFError / ConnectionError 类异常：静默处理（客户端断连是正常行为）
    - 对其他异常（如编码错误、认证错误）：保留原始日志以便排查真正的 bug
    """
    def _authenticate_and_serve_client(self, sock):
        try:
            if self.authenticator:
                addrinfo = sock.getpeername()
                try:
                    sock2, credentials = self.authenticator(sock)
                except Exception:
                    self.logger.info("authentication failed, rejecting connection")
                    return
                else:
                    self.logger.info("authenticated successfully")
            else:
                credentials = None
                sock2 = sock
            try:
                self._serve_client(sock2, credentials)
            except (EOFError, ConnectionError, ConnectionAbortedError, 
                    ConnectionResetError, BrokenPipeError, OSError):
                # ── 客户端断连 = 管线正常行为，完全静默 ──
                pass
            except Exception:
                # ── 真正的异常，保留日志但不 raise（避免 threading 二次打印）──
                self.logger.exception("client connection terminated with unexpected error")
        finally:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            closing(sock)
            self.clients.discard(sock)


def _start_rpyc_server(port=18812):
    """在 Maya 的后台线程中启动一个纯粹的 RPyC 服务"""
    existing_servers = getattr(sys, '_cgi_rpyc_servers', {})
    if not isinstance(existing_servers, dict):
        existing_servers = {}
    if port in existing_servers:
        return

    server = _SilentThreadedServer(
        _GracefulClassicService, 
        port=port, 
        protocol_config={
            'allow_all_attrs': True, 
            'allow_getattr': True, 
            'allow_setattr': True,
            'allow_delattr': True,
            'allow_builtins': True,
            'allow_dict': True,
            'sync_request_timeout': 3600  # 给重度 Pipeline 任务留出 1 小时超时
        }
    )
    existing_servers[port] = server
    sys._cgi_rpyc_servers = existing_servers
    # 兼容旧探针：不要再用这个单值标记阻止其他 foreground 端口启动。
    sys._cgi_rpyc_server = server

    def run_server():
        try:
            server.start()
        except Exception as e:
            import traceback
            traceback.print_exc()

    t = threading.Thread(target=run_server, name="CGI_RPyC_Server")
    t.daemon = True
    t.start()
    
    print(f"CGI RPyC Server started on port {port}")
