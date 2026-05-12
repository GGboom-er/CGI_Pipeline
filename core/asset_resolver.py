# core/asset_resolver.py
# ── CGI Pipeline v3.0 — 统一资产定位层 ──
#
# 路径拼接公式（asset）:
#   {server_root}/{path_roots[root_key]}/{category}/{asset}/{stage}/{task}/
# 路径拼接公式（shot）:
#   {server_root}/{path_roots.shots}/{sequence}/{shot}/{stage}/{task}/
#
# 职责：
#   1. 定位源文件（asset + shot）
#   2. 计算任务沙盒候选路径（历史配置键 ai_publish_root，实际指向 runs）
#   3. 原子版本递增（filelock）
#   4. 路径解析（从文件名反推 project/category/asset/stage/task）

import json
import re
import os
from pathlib import Path
from filelock import FileLock, Timeout

_VERSION_PATTERN = re.compile(r'_v(\d{3,4})(?=\.|_|$)')
_FILENAME_PATTERN = re.compile(
    r'^(?P<project>[^_]+)_(?P<category>[^_]+)_(?P<asset>[^_]+)_'
    r'(?P<stage>[^_]+)_(?P<task>[^_]+)_v(?P<version>\d{3,4})\.(?P<ext>\w+)$'
)
_LOCK_TIMEOUT = 30


class AssetResolver:
    """
    统一资产定位入口。

    初始化时传入项目配置 dict（来自 config_loader），
    所有路径规则由配置驱动，代码零硬编码。
    """

    def __init__(self, project_config: dict):
        self.config = project_config
        self.project = project_config.get('project_name', 'default')

        server = project_config.get('server_root', '')
        path_roots = project_config.get('path_roots', {})
        self._server_root = Path(server) if server else Path('')
        self._path_roots = path_roots

        # 兼容旧配置：如果没有 server_root 则回退到 source_root
        if server and 'assets' in path_roots:
            self.source_root = self._server_root / path_roots['assets']
        else:
            self.source_root = Path(project_config.get('source_root', ''))

        self.ai_publish_root = Path(project_config.get('ai_publish_root', ''))
        self._ai_path_roots = project_config.get('ai_path_roots', {})

        tex_rel = path_roots.get('sourceimages', '')
        if server and tex_rel:
            self.texture_root = self._server_root / tex_rel
        else:
            self.texture_root = Path(project_config.get('texture_root', ''))

        self._stages_cfg = project_config.get('stages', {})
        self._categories_cfg = project_config.get('categories', {})
        self._shots_cfg = project_config.get('shots', {})
        self._pipelines = project_config.get('pipelines', {})

    # ═══════════════════════════════════════
    # 核心路径拼接
    # ═══════════════════════════════════════

    def _get_root(self, root_key: str) -> Path:
        rel = self._path_roots.get(root_key, '')
        if rel:
            return self._server_root / rel
        return self.source_root

    def _get_primary_task(self, stage: str) -> str:
        stage_cfg = self._stages_cfg.get(stage, {})
        return stage_cfg.get('primary_task', f'{stage}Master')

    def _get_stage_path_root(self, stage: str) -> Path:
        stage_cfg = self._stages_cfg.get(stage, {})
        override = stage_cfg.get('path_root_override')
        if override:
            return self._get_root(override)
        cat_root_key = 'assets'
        return self._get_root(cat_root_key)

    def _resolve_stage_dir(self, category: str, asset: str,
                           stage: str, task: str | None = None) -> Path:
        root = self._get_stage_path_root(stage)
        t = task or self._get_primary_task(stage)
        
        # 1. Try stage-specific pattern override
        stage_cfg = self._stages_cfg.get(stage, {})
        pattern = stage_cfg.get('path_pattern')
        
        # 2. Fallback to global asset pattern
        if not pattern:
            pattern = self.config.get('path_pattern_asset', '{category}/{asset}/{stage}/{task}')
            
        rel_path = pattern.format(
            category=category, asset=asset, stage=stage, task=t, project=self.project
        )
        return root / rel_path

    def _get_ai_stage_root(self, stage: str) -> Path:
        stage_cfg = self._stages_cfg.get(stage, {})
        override = stage_cfg.get('path_root_override')
        if override:
            ai_rel = self._ai_path_roots.get(override, override)
        else:
            ai_rel = self._ai_path_roots.get('assets', 'assets')
        return self.ai_publish_root / ai_rel

    def _resolve_ai_stage_dir(self, category: str, asset: str,
                              stage: str, task: str | None = None) -> Path:
        root = self._get_ai_stage_root(stage)
        t = task or self._get_primary_task(stage)
        
        # 1. Try stage-specific pattern override
        stage_cfg = self._stages_cfg.get(stage, {})
        pattern = stage_cfg.get('path_pattern')
        
        # 2. Fallback to global asset pattern
        if not pattern:
            pattern = self.config.get('path_pattern_asset', '{category}/{asset}/{stage}/{task}')
            
        rel_path = pattern.format(
            category=category, asset=asset, stage=stage, task=t, project=self.project
        )
        return root / rel_path

    # ═══════════════════════════════════════
    # 源文件定位（只读）— Asset
    # ═══════════════════════════════════════

    def resolve_latest(self, category: str, asset: str,
                       stage: str, task: str | None = None) -> Path | None:
        stage_dir = self._resolve_stage_dir(category, asset, stage, task)
        return self._find_latest_in_dir(stage_dir)

    def resolve_version(self, category: str, asset: str,
                        stage: str, version: int,
                        task: str | None = None) -> Path | None:
        stage_dir = self._resolve_stage_dir(category, asset, stage, task)
        pattern = f'*_v{version:03d}.*'
        matches = list(stage_dir.glob(pattern)) if stage_dir.exists() else []
        ma_files = [f for f in matches if f.suffix in ('.ma', '.mb')]
        return ma_files[0] if ma_files else (matches[0] if matches else None)

    def _get_pipeline_stages(self, pipeline: str | None,
                              category: str) -> list[str]:
        if pipeline and pipeline in self._pipelines:
            return self._pipelines[pipeline].get('stages', [])
        cat_cfg = self._categories_cfg.get(category, {})
        cat_stages = cat_cfg.get('stages', [])
        return list(reversed(cat_stages))

    def resolve_by_stage(self, category: str, asset: str,
                         pipeline: str | None = None,
                         ext_filter: list[str] | None = None) -> dict | None:
        stages = self._get_pipeline_stages(pipeline, category)
        for stage in stages:
            stage_dir = self._resolve_stage_dir(category, asset, stage)
            if not stage_dir.exists():
                continue
            latest = self._find_latest_in_dir(stage_dir, ext_filter)
            if latest:
                ver_match = _VERSION_PATTERN.search(latest.name)
                ver_num = int(ver_match.group(1)) if ver_match else 0
                texture_dir = self.texture_root / category / asset if self.texture_root.exists() else None
                return {
                    'asset': asset,
                    'stage': stage,
                    'task': self._get_primary_task(stage),
                    'version': latest.name,
                    'version_num': ver_num,
                    'path': str(latest),
                    'texture_dir': str(texture_dir) if texture_dir else None,
                    'valid': True,
                }
        return None

    def list_versions(self, category: str, asset: str,
                      stage: str, task: str | None = None) -> list[dict]:
        stage_dir = self._resolve_stage_dir(category, asset, stage, task)
        if not stage_dir.exists():
            return []
        versions = []
        for f in sorted(stage_dir.iterdir()):
            m = _VERSION_PATTERN.search(f.name)
            if m and f.suffix in ('.ma', '.mb', '.abc', '.blend'):
                versions.append({
                    'version': int(m.group(1)),
                    'path': str(f),
                    'name': f.name,
                    'exists': True,
                })
        return versions

    def scan_category(self, category: str,
                      ext_filter: list[str] | None = None) -> list[dict]:
        cat_dir = self.source_root / category
        if not cat_dir.exists():
            return []
        results = []
        for item in sorted(cat_dir.iterdir()):
            if not item.is_dir():
                continue
            resolved = self.resolve_by_stage(category, item.name, ext_filter)
            if resolved:
                results.append(resolved)
            else:
                results.append({
                    'asset': item.name, 'stage': 'N/A',
                    'version': 'ERROR: Empty Asset', 'version_num': 0,
                    'path': None, 'texture_dir': None, 'valid': False,
                })
        return results

    # ═══════════════════════════════════════
    # 源文件定位（只读）— Shot
    # ═══════════════════════════════════════

    def _get_shot_primary_task(self, stage: str) -> str:
        shot_stages = self._shots_cfg.get('stages', {})
        stage_cfg = shot_stages.get(stage, {})
        return stage_cfg.get('primary_task', f'{stage}Master')

    def _get_shot_stage_root(self, stage: str) -> Path:
        shot_stages = self._shots_cfg.get('stages', {})
        stage_cfg = shot_stages.get(stage, {})
        override = stage_cfg.get('path_root_override')
        if override:
            return self._get_root(override)
        root_key = self._shots_cfg.get('path_root', 'shots')
        return self._get_root(root_key)

    def _resolve_shot_dir(self, sequence: str, shot: str,
                          stage: str, task: str | None = None) -> Path:
        root = self._get_shot_stage_root(stage)
        t = task or self._get_shot_primary_task(stage)
        
        # 1. Try stage-specific pattern override
        shot_stages = self._shots_cfg.get('stages', {})
        stage_cfg = shot_stages.get(stage, {})
        pattern = stage_cfg.get('path_pattern')
        
        # 2. Fallback to global shot pattern
        if not pattern:
            pattern = self.config.get('path_pattern_shot', '{sequence}/{shot}/{stage}/{task}')
            
        rel_path = pattern.format(
            sequence=sequence, shot=shot, stage=stage, task=t, project=self.project
        )
        return root / rel_path

    def resolve_shot_latest(self, sequence: str, shot: str,
                            stage: str,
                            task: str | None = None) -> Path | None:
        shot_dir = self._resolve_shot_dir(sequence, shot, stage, task)
        return self._find_latest_in_dir(shot_dir)

    def list_shot_versions(self, sequence: str, shot: str,
                           stage: str,
                           task: str | None = None) -> list[dict]:
        shot_dir = self._resolve_shot_dir(sequence, shot, stage, task)
        if not shot_dir.exists():
            return []
        versions = []
        for f in sorted(shot_dir.iterdir()):
            m = _VERSION_PATTERN.search(f.name)
            if m and f.suffix in ('.ma', '.mb', '.abc', '.blend'):
                versions.append({
                    'version': int(m.group(1)),
                    'path': str(f),
                    'name': f.name,
                    'exists': True,
                })
        return versions

    # ═══════════════════════════════════════
    # 任务沙盒候选路径（历史函数名保留兼容，实际写入 runs/沙盒）
    # ═══════════════════════════════════════

    def resolve_next_publish(self, category: str, asset: str,
                             stage: str,
                             task: str | None = None) -> tuple[int, Path]:
        """
        计算 runs/沙盒根下的下一个版本号和路径。
        使用 filelock 保证并发安全。
        """
        t = task or self._get_primary_task(stage)
        pub_dir = self._resolve_ai_stage_dir(category, asset, stage, task)
        pub_dir.mkdir(parents=True, exist_ok=True)
        lock_path = pub_dir / '.version.lock'

        try:
            with FileLock(str(lock_path), timeout=_LOCK_TIMEOUT):
                existing = self._scan_versions(pub_dir)
                next_ver = max(existing, default=0) + 1
                filename = (
                    f'{self.project}_{category}_{asset}_{stage}_{t}'
                    f'_v{next_ver:03d}.ma'
                )
                pub_path = pub_dir / filename
                pub_path.touch()
                return next_ver, pub_path
        except Timeout:
            raise RuntimeError(
                f'版本锁超时（{_LOCK_TIMEOUT}s），可能有并发任务: {pub_dir}'
            )

    def build_publish_path(self, category: str, asset: str,
                           stage: str, version: int,
                           task: str | None = None) -> Path:
        t = task or self._get_primary_task(stage)
        pub_dir = self._resolve_ai_stage_dir(category, asset, stage, task)
        filename = (
            f'{self.project}_{category}_{asset}_{stage}_{t}'
            f'_v{version:03d}.ma'
        )
        return pub_dir / filename

    # ═══════════════════════════════════════
    # 路径解析（从文件名反推）
    # ═══════════════════════════════════════

    @staticmethod
    def parse_filename(filename: str) -> dict | None:
        m = _FILENAME_PATTERN.match(filename)
        if m:
            return m.groupdict()
        return None

    def parse_path(self, filepath: str | Path) -> dict | None:
        p = Path(filepath)
        parsed = self.parse_filename(p.name)
        if parsed:
            return parsed
        parts = p.parts
        if len(parts) >= 4:
            return {
                'category': parts[-4] if len(parts) >= 5 else None,
                'asset': parts[-3] if len(parts) >= 5 else parts[-4],
                'stage': parts[-2] if len(parts) >= 4 else None,
                'task': parts[-1] if len(parts) >= 3 else None,
            }
        return None

    # ═══════════════════════════════════════
    # 查询辅助
    # ═══════════════════════════════════════

    def get_stage_info(self, stage: str) -> dict:
        return self._stages_cfg.get(stage, {})

    def get_category_stages(self, category: str) -> list[str]:
        cat_cfg = self._categories_cfg.get(category, {})
        if isinstance(cat_cfg, dict):
            return cat_cfg.get('stages', [])
        return list(self._stages_cfg.keys())

    def get_shot_stages(self) -> dict:
        return self._shots_cfg.get('stages', {})

    # ═══════════════════════════════════════
    # 内部工具
    # ═══════════════════════════════════════

    def _find_latest_in_dir(self, stage_dir: Path,
                            ext_filter: list[str] | None = None) -> Path | None:
        if not stage_dir.exists():
            return None
        best_ver = -1
        best_file = None
        for f in stage_dir.iterdir():
            if not f.is_file():
                continue
            if ext_filter and f.suffix not in ext_filter:
                continue
            if f.suffix not in ('.ma', '.mb', '.abc', '.blend', '.fbx'):
                continue
            m = _VERSION_PATTERN.search(f.name)
            if m:
                ver = int(m.group(1))
                if ver > best_ver:
                    best_ver = ver
                    best_file = f
        return best_file

    @staticmethod
    def _scan_versions(directory: Path) -> list[int]:
        versions = []
        if not directory.exists():
            return versions
        for f in directory.iterdir():
            if not f.is_file():
                continue
            m = _VERSION_PATTERN.search(f.name)
            if m:
                versions.append(int(m.group(1)))
        return versions

    def _find_latest_in_subdir(self, stage_dir: Path, subdir: str,
                               ext: str) -> str | None:
        """查找 {stage_dir}/{subdir}/ 下最新版本的指定后缀文件。"""
        target_dir = stage_dir / subdir
        if not target_dir.exists():
            return None
        best_ver = -1
        best_file = None
        for f in target_dir.iterdir():
            if f.suffix != ext:
                continue
            m = _VERSION_PATTERN.search(f.name)
            if m:
                ver = int(m.group(1))
                if ver > best_ver:
                    best_ver = ver
                    best_file = f
        return str(best_file) if best_file else None

    def _find_latest_info_json(self, stage_dir: Path) -> str | None:
        """查找 {stage_dir}/.info/ 下最新版本的 JSON 文件。"""
        return self._find_latest_in_subdir(stage_dir, '.info', '.json')

    def _find_latest_abc(self, stage_dir: Path) -> str | None:
        """查找 {stage_dir}/.cache/ 下最新版本的 ABC 文件。"""
        return self._find_latest_in_subdir(stage_dir, '.cache', '.abc')

    # ═══════════════════════════════════════
    # MCP 统一入口（逻辑下沉，reload 即生效）
    # ═══════════════════════════════════════

    def resolve_for_mcp(self, category: str, asset_name: str,
                        stage: str = '', task: str = '',
                        pipeline: str = '') -> dict:
        task_val = task or None
        pipeline_val = pipeline or None

        if stage:
            latest = self.resolve_latest(category, asset_name, stage, task_val)
            versions = self.list_versions(category, asset_name, stage, task_val)
            stage_info = self.get_stage_info(stage)
            stage_dir = self._resolve_stage_dir(category, asset_name, stage, task_val)
            info_json = self._find_latest_info_json(stage_dir)
            abc_cache = self._find_latest_abc(stage_dir)
            return {
                'category': category,
                'asset_name': asset_name,
                'stage': stage,
                'task': task_val or stage_info.get('primary_task', ''),
                'latest_publish': str(latest) if latest else None,
                'latest_exists': latest.exists() if latest else False,
                'info_json': info_json,
                'abc_cache': abc_cache,
                'versions': versions,
                'total_versions': len(versions),
                'stage_info': stage_info,
            }

        resolved = self.resolve_by_stage(category, asset_name, pipeline=pipeline_val)
        if resolved:
            # 补充 info_json
            stage_name = resolved.get('stage', '')
            if stage_name:
                sd = self._resolve_stage_dir(category, asset_name, stage_name)
                resolved['info_json'] = self._find_latest_info_json(sd)
                resolved['abc_cache'] = self._find_latest_abc(sd)
            return {
                'category': category,
                'asset_name': asset_name,
                'pipeline': pipeline or '',
                **resolved,
            }
        # 收集已搜索路径以便调试
        searched = []
        stages_tried = self._get_pipeline_stages(pipeline_val, category)
        for stage_name in stages_tried:
            sd = self._resolve_stage_dir(category, asset_name, stage_name)
            searched.append({'stage': stage_name, 'path': str(sd), 'exists': sd.exists()})
        return {
            'category': category,
            'asset_name': asset_name,
            'pipeline': pipeline or '',
            'latest_publish': None,
            'message': '未找到任何阶段的资产文件',
            'searched_paths': searched,
        }
