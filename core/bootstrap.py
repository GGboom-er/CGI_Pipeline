# core/bootstrap.py
# ── CGI Pipeline 统一配置入口 ──
#
# 自动加载 .env，暴露所有 DCC 路径和项目配置。
# 所有模块只需 `from core.bootstrap import cfg` 即可拿到一切。
#
# 用法:
#   from core.bootstrap import cfg
#   cfg.BLENDER_PATH        → .env 配置的 Blender 可执行文件路径
#   cfg.MAYAPY_PATH          → .env 配置的 mayapy 路径
#   ai_publish_root（历史配置键）→ 项目 runs/任务沙盒根路径
#   cfg.launch_blender(blend_file, script)  → 启动 Blender 子进程
#   cfg.localize(server_path, project)      → 拷贝服务器文件到本地 runs

import os
import sys
import shutil
import subprocess
from pathlib import Path


class _Cfg:
    """管线全局配置单例。import 时自动初始化。"""

    def __init__(self):
        # ── PROJECT_ROOT：先从 .env 所在目录推导 ──
        self._root = Path(__file__).resolve().parent.parent
        self.PROJECT_ROOT = str(self._root)

        # 确保 PROJECT_ROOT 在 sys.path 中
        if self.PROJECT_ROOT not in sys.path:
            sys.path.insert(0, self.PROJECT_ROOT)

        # 注入 vendor/ 第三方依赖（trimesh/rtree 等），即插即用无需手动安装
        vendor_dir = str(self._root / 'vendor')
        if os.path.isdir(vendor_dir) and vendor_dir not in sys.path:
            sys.path.append(vendor_dir)

        # ── 加载 .env ──
        self._load_env()

        # ── DCC 路径（从 .env 读取，不猜测） ──
        self.BLENDER_PATH = os.environ.get('BLENDER_PATH', '')
        self.MAYAPY_PATH = os.environ.get('MAYAPY_PATH', '')
        self.MAYA_BIN_DIR = os.environ.get('MAYA_BIN_DIR', '')
        self.DEFAULT_PROJECT = os.environ.get('DEFAULT_PROJECT', 'ysj')

    def _load_env(self):
        """解析 .env 文件并注入 os.environ（不覆盖已存在的环境变量）"""
        env_path = self._root / '.env'
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()
            # 不覆盖已存在的环境变量
            if key not in os.environ:
                os.environ[key] = value

    # ── 项目配置代理 ──

    def project_config(self, project=''):
        """加载项目配置 dict"""
        from core.config_loader import load_project_config
        return load_project_config(project or self.DEFAULT_PROJECT)

    def server_root(self, project=''):
        """获取项目的服务器根路径"""
        from core.config_loader import get_server_root
        return get_server_root(project or self.DEFAULT_PROJECT)

    # ── DCC 启动 ──

    def launch_blender(self, blend_file, script_path, timeout=600):
        """启动 Blender 后台执行脚本。

        Args:
            blend_file: .blend 文件路径
            script_path: 要执行的 Python 脚本路径
            timeout: 超时秒数（默认 10 分钟）

        Returns:
            subprocess.CompletedProcess
        """
        if not self.BLENDER_PATH:
            raise RuntimeError('BLENDER_PATH 未配置（检查 .env）')
        if not os.path.isfile(self.BLENDER_PATH):
            raise FileNotFoundError(f'Blender 可执行文件不存在: {self.BLENDER_PATH}')

        cmd = [self.BLENDER_PATH, '--background', blend_file, '--python', script_path]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def launch_mayapy(self, script_path, args=None, timeout=600):
        """启动 mayapy 执行脚本。

        Args:
            script_path: 要执行的 Python 脚本路径
            args: 额外命令行参数列表
            timeout: 超时秒数

        Returns:
            subprocess.CompletedProcess
        """
        if not self.MAYAPY_PATH:
            raise RuntimeError('MAYAPY_PATH 未配置（检查 .env）')
        if not os.path.isfile(self.MAYAPY_PATH):
            raise FileNotFoundError(f'mayapy 可执行文件不存在: {self.MAYAPY_PATH}')

        cmd = [self.MAYAPY_PATH, script_path] + (args or [])
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


    def __repr__(self):
        return (
            f'PipelineConfig(\n'
            f'  PROJECT_ROOT={self.PROJECT_ROOT}\n'
            f'  BLENDER_PATH={self.BLENDER_PATH}\n'
            f'  MAYAPY_PATH={self.MAYAPY_PATH}\n'
            f'  DEFAULT_PROJECT={self.DEFAULT_PROJECT}\n'
            f')'
        )


# ── 模块级单例 ──
cfg = _Cfg()

# 向后兼容：旧代码 `from core.bootstrap import PROJECT_ROOT`
PROJECT_ROOT = cfg.PROJECT_ROOT
