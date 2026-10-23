"""Copy only schema v3 Color/Alpha files and rewrite a separate material JSON."""

import filecmp
import hashlib
import json
import os
import time
from pathlib import Path

from cgi_pipeline.capabilities.pipeline.pipeline.copy_files.copy_files import execute as copy_files
from cgi_pipeline.core.path_guard import is_protected_path
from cgi_pipeline.core.receipt import make_item, make_receipt


def _path(value, label):
    if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
        raise ValueError(f"{label} 必须为非空绝对路径")
    return Path(value).resolve()


def _key(path):
    return os.path.normcase(str(path))


def _allow_output(path, run_dir):
    if not path.is_relative_to(run_dir) or is_protected_path(str(path)):
        raise PermissionError(f"输出必须位于当前任务沙盒且不在受保护区域: {path}")


def _references(raw):
    if not isinstance(raw, dict) or raw.get("schema_version") != 3:
        raise ValueError("只支持 blender_extract_materials schema v3")
    materials = raw.get("materials")
    if not isinstance(materials, dict) or not materials:
        raise ValueError("schema v3 materials 必须为非空对象")
    references = []
    for name, material in materials.items():
        if not isinstance(material, dict):
            raise ValueError(f"材质 {name!r} 必须为对象")
        for channel, allowed in (("color", {"solid", "texture"}), ("alpha", {"value", "texture"})):
            info = material.get(channel, {"type": "value", "value": 1.0} if channel == "alpha" else {})
            if not isinstance(info, dict) or info.get("type") not in allowed:
                raise ValueError(f"材质 {name!r} 的 {channel} 类型无效")
            if info["type"] == "texture":
                source = _path(info.get("path"), f"{name}.{channel}.path")
                if not source.is_file():
                    raise ValueError(f"引用的贴图不存在或不是文件: {source}")
                references.append((info, source))
    return references


def execute(payload):
    started = time.time()
    params = payload.get("parameters", {})
    try:
        run_dir = _path(payload.get("run_dir"), "context.run_dir")
        if not run_dir.is_dir():
            raise ValueError(f"当前任务沙盒不存在: {run_dir}")
        input_path = _path(params.get("materials_path"), "materials_path")
        output_path = _path(params.get("output_path"), "output_path")
        texture_dir = _path(params.get("texture_dir"), "texture_dir")
        for path in (output_path, texture_dir):
            _allow_output(path, run_dir)
        if output_path == input_path:
            raise ValueError("output_path 不得覆盖输入材质 JSON")
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        references = _references(raw)
        sources = {_key(source): source for _, source in references}
        basenames = {}
        for key, source in sources.items():
            basenames.setdefault(os.path.normcase(source.name), set()).add(key)
        destinations, occupied = {}, {}
        for key, source in sources.items():
            directory = texture_dir
            if len(basenames[os.path.normcase(source.name)]) > 1:
                directory /= hashlib.sha256(_key(source.parent).encode("utf-8")).hexdigest()[:12]
            destination = (directory / source.name).resolve()
            _allow_output(destination, run_dir)
            dst_key = _key(destination)
            if dst_key in occupied and occupied[dst_key] != key:
                raise ValueError(f"贴图目标冲突: {destination}")
            if destination == output_path or dst_key in sources:
                raise ValueError(f"贴图目标不得覆盖输入文件或输出 JSON: {destination}")
            if destination.exists() and (not destination.is_file() or not filecmp.cmp(source, destination, shallow=False)):
                raise ValueError(f"贴图目标已有不同内容，请使用新的打包目录: {destination}")
            destinations[key], occupied[dst_key] = destination, key
        if _key(output_path) in sources:
            raise ValueError("output_path 不得覆盖源贴图")
        copied = reused = total_bytes = 0
        for key, source in sources.items():
            receipt = copy_files({"parameters": {
                "source": str(source), "destination": str(destinations[key]), "overwrite": False,
            }})
            if receipt.get("status") != "SUCCESS":
                raise OSError(receipt.get("error") or f"贴图复制失败: {source}")
            result = receipt["output"]["result"]
            copied += result["copied_count"]
            reused += result["skipped_count"]
            total_bytes += result["total_bytes"]
        mapping = {}
        for info, source in references:
            destination = destinations[_key(source)].as_posix()
            mapping[info["path"]] = destination
            info["path"] = destination
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return make_receipt(
            "package_textures", "SUCCESS", started,
            summary_input=input_path.name, summary_action=f"→ {texture_dir}",
            summary_count=len(sources), summary_label="引用贴图",
            items=[make_item(source, destination) for source, destination in mapping.items()],
            output={
                "output_path": output_path.as_posix(), "texture_dir": texture_dir.as_posix(),
                "path_mapping": mapping, "result": {
                    "reference_count": len(references), "texture_count": len(sources),
                    "copied_count": copied, "reused_count": reused, "total_bytes": total_bytes,
                },
            },
        )
    except PermissionError as exc:
        return make_receipt("package_textures", "BLOCKED", started, error=str(exc))
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        return make_receipt("package_textures", "ERROR", started, error=str(exc))
