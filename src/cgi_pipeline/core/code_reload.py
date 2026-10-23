# cgi_pipeline/core/code_reload.py
# ── 常驻 DCC 宿主的 capability 代码热重载 ──
#
# capability 代码在 DCC 进程内执行，而 warm pool 会复用该进程；进程只在首次
# import 时读文件，因此改完源码不处理就仍跑旧版（见 lesson L118）。
#
# 本模块提供两件事：
#   source_fingerprint()  —— 派活前算一次源码指纹，交给调度侧比对
#   evict_capability_modules() —— 在宿主进程内剔除 cgi_pipeline 模块
#
# 为什么剔除而不是 importlib.reload：reload 原地改模块对象，别处已绑走的旧函数
# 引用不会更新。CGI 的 catalog.load_handler 每次调用都重新 import_module，所以
# 从 sys.modules 剔除后下一次执行自然拿到全新模块，没有半新半旧的中间态。

import os
import sys
import importlib
import threading
from contextlib import contextmanager

# 只热重载第一方代码；bpy / maya.cmds 与已打开的场景是宿主自身状态，绝不能碰。
_RELOAD_PREFIX = 'cgi_pipeline.'

# 指纹只覆盖会改变执行行为的第一方源码目录。
_FINGERPRINT_DIRS = ('capabilities', 'core')

RELOAD_COMMAND = '__RELOAD__'

_foreground_version = None
_foreground_lock = threading.RLock()


def _refresh_foreground_capabilities():
    current = [name for name in list(sys.modules)
               if name == 'cgi_pipeline.capabilities'
               or name.startswith('cgi_pipeline.capabilities.')]
    # Drop stale timestamp-based bytecode, including same-size edits within a second.
    for name in current:
        cached = getattr(sys.modules[name], '__cached__', None)
        if cached and os.path.isfile(cached):
            os.remove(cached)
    # Capability modules import each other's functions; reload the closure together.
    for name in sorted(current, key=len, reverse=True):
        module = sys.modules.pop(name, None)
        parent_name, _, child = name.rpartition('.')
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, child, None) is module:
            delattr(parent, child)
    importlib.invalidate_caches()


@contextmanager
def capability_session():
    """Pin the catalog and refresh capability code only on a published version.

    Publish implementation edits by bumping the manifest version and rebuilding
    the catalog. Framework changes require a controlled host restart.
    """
    from cgi_pipeline import catalog

    global _foreground_version
    with _foreground_lock:
        snapshot = catalog.snapshot()
        if snapshot[1] != _foreground_version:
            _refresh_foreground_capabilities()
            _foreground_version = snapshot[1]
        yield snapshot


def _source_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source_fingerprint() -> str:
    """算第一方源码指纹（文件数 + 最新 mtime + 总字节）。

    生产环境源码不变则指纹不变，永不触发重载；开发期改任意 .py 即变。
    只 stat 不读内容，成本是一次目录遍历。
    """
    root = _source_root()
    count = 0
    newest = 0.0
    total = 0
    for sub in _FINGERPRINT_DIRS:
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for name in filenames:
                if not name.endswith('.py'):
                    continue
                try:
                    st = os.stat(os.path.join(dirpath, name))
                except OSError:
                    continue
                count += 1
                total += st.st_size
                if st.st_mtime > newest:
                    newest = st.st_mtime
    return f"{count}:{newest:.3f}:{total}"


def evict_capability_modules() -> int:
    """在宿主进程内剔除第一方模块，下次 import 时全新加载。

    返回剔除的模块数。调用方负责在剔除后不要继续使用已持有的旧引用。
    """
    stale = [
        name for name in list(sys.modules)
        if name.startswith(_RELOAD_PREFIX)
        # 本模块自身留下：它正在执行，剔除它没有意义
        and name != __name__
    ]
    for name in stale:
        sys.modules.pop(name, None)
    return len(stale)
