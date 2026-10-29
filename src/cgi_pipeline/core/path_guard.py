# cgi_pipeline/core/path_guard.py
# ── 服务器路径保护 — AI 执行第一性原则 ──
#
# 规则：如果当前场景路径（scene name）指向受保护的服务器根路径，
#       则禁止任何形式的 cmds.file(save=True) 操作。
#
# 只读盘符和 UNC 前缀从 pipeline_manifest.json 读取（唯一真相源）。
# 项目级 protected_roots 从 config_loader 读取。
#
# 用法：
#   from cgi_pipeline.core.path_guard import assert_save_allowed, is_protected_path
#   assert_save_allowed(save_path)  # 不通过则抛 ProtectedPathError

import os
import json
from pathlib import Path

from cgi_pipeline.core.bootstrap import cfg as _cfg
from cgi_pipeline.core.manifest import get_readonly_drives, get_readonly_unc_prefixes, normalize_path

# ─── 配置加载 ───

_PROJECT_ROOT = Path(_cfg.PROJECT_ROOT)
_CONFIG_DIR = _PROJECT_ROOT / 'config'
_PROTECTED_ROOTS: list[str] = []


def _load_protected_roots() -> list[str]:
    """从项目配置文件中加载受保护的服务器路径列表"""
    global _PROTECTED_ROOTS
    if _PROTECTED_ROOTS:
        return _PROTECTED_ROOTS

    roots = set()
    if _CONFIG_DIR.exists():
        for cfg_file in _CONFIG_DIR.glob('*_config*.json'):
            try:
                data = json.loads(cfg_file.read_text(encoding='utf-8'))
                for r in data.get('protected_roots', []):
                    roots.add(_normalize(r))
            except Exception:
                pass

    _PROTECTED_ROOTS = list(roots)
    return _PROTECTED_ROOTS


_normalize = normalize_path


def _within(norm: str, root: str) -> bool:
    """按路径段比较：'//server' 只覆盖自身与 '//server/...'，不覆盖 '//serverbackup'。

    裸盘符根 'x:' 还覆盖盘符相对路径 'x:a'。两参数都须已经过 _normalize。
    """
    if len(root) == 2 and root[1] == ':':
        return norm[:2] == root
    return norm == root or norm.startswith(root + '/')


# ─── 公开 API ───

class ProtectedPathError(RuntimeError):
    """试图保存到受保护的服务器路径"""
    pass


def is_protected_path(path: str) -> bool:
    """检查路径是否落在受保护的服务器根路径下

    Args:
        path: 要检查的文件路径（支持任意斜杠/大小写）

    Returns:
        True = 受保护，禁止保存
    """
    if not path:
        return False
    norm = _normalize(path)
    if _under_readonly_root(norm):
        return True
    return any(_within(norm, root) for root in _load_protected_roots())


def _under_readonly_root(norm: str) -> bool:
    """manifest 只读盘符与 UNC 前缀；norm 须已经过 _normalize。"""
    roots = (*get_readonly_drives(), *get_readonly_unc_prefixes())
    return any(_within(norm, root) for root in roots)


def describe_protected_roots() -> list[str]:
    """服务说明用的受保护根清单，与 is_protected_path 使用同一份规范化结果。

    manifest 盘符显示为 'X:/'，UNC 显示为 '//server/'；项目配置里已被它们覆盖的根不重复列出。
    """
    labels = [drive.upper() + '/' for drive in sorted(get_readonly_drives())]
    labels += [prefix + '/' for prefix in get_readonly_unc_prefixes()]
    labels += sorted(_label(root) for root in _load_protected_roots() if not _under_readonly_root(root))
    return labels


def _label(root: str) -> str:
    return (root[0].upper() + root[1:] if root[1:2] == ':' else root) + '/'


def assert_save_allowed(save_path: str, context: str = '') -> None:
    """断言保存路径不在受保护区域

    如果路径受保护，生成拦截报告并抛出 ProtectedPathError。

    Args:
        save_path: 目标保存路径
        context: 调用上下文（如 api_id），用于错误信息追踪

    Raises:
        ProtectedPathError: 路径受保护时抛出
    """
    if is_protected_path(save_path):
        report_path = generate_block_report(save_path, context)
        ctx = f'（来自 {context}）' if context else ''
        raise ProtectedPathError(
            f'🛑 保存被拦截{ctx}：目标路径 "{save_path}" '
            f'位于受保护的服务器区域。\n'
            f'受保护根路径：{_load_protected_roots()}\n'
            f'拦截报告：{report_path}\n'
            f'请指定一个本地/私有路径作为 save_path。'
        )


def get_scene_protection_report() -> dict:
    """获取当前打开场景的保护状态报告

    在 Maya 环境内调用，返回当前场景是否受保护的结构化报告。

    Returns:
        dict: {'scene_path': str, 'is_protected': bool, 'protected_roots': list}
    """
    try:
        import maya.cmds as cmds
        scene = cmds.file(query=True, sceneName=True) or ''
    except ImportError:
        scene = ''

    return {
        'scene_path': scene,
        'is_protected': is_protected_path(scene),
        'protected_roots': _load_protected_roots(),
    }



def generate_block_report(save_path: str, context: str = '') -> str:
    """生成路径拦截报告到 reports/ 目录，返回报告路径"""
    import datetime
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report_dir = _PROJECT_ROOT / 'reports'
    report_dir.mkdir(parents=True, exist_ok=True)
    safe_name = save_path.replace('/', '_').replace('\\', '_').replace(':', '')[:80]
    report_path = report_dir / f'block_{safe_name}_{int(datetime.datetime.now().timestamp())}.md'

    suggestion_line = '请手动指定一个本地测试路径或沙盒路径（如 Y:/GGbommer/scripts/CGI_Pipeline/runs/xxx）'

    lines = [
        '# 🛑 路径保护拦截报告',
        '',
        '| 项目 | 值 |',
        '|---|---|',
        f'| 拦截时间 | {now} |',
        f'| 目标路径 | `{save_path}` |',
        f'| 调用上下文 | {context or "未知"} |',
        f'| 受保护根路径 | {_load_protected_roots()} |',
        '',
        '## 原因',
        '',
        '目标保存路径位于受保护的服务器区域，为防止 AI 操作意外覆盖生产数据，保存已被拦截。',
        '',
        '## 建议替代路径',
        '',
        suggestion_line,
        '',
        '---',
        '*由 CGI Pipeline path_guard 自动生成*',
    ]

    report_path.write_text('\n'.join(lines), encoding='utf-8')
    return str(report_path)
