# core/material_semantics.py
# 材质贴图语义判定。仅做文件名级别的保守过滤，不猜测不存在的贴图用途。

import os
import re


_NON_COLOR_TEXTURE_TOKENS = {
    "ao",
    "ambientocclusion",
    "occlusion",
    "normal",
    "norm",
    "nor",
    "nrm",
    "roughness",
    "rough",
    "metallic",
    "metalness",
    "specular",
    "spec",
    "gloss",
    "glossiness",
    "height",
    "bump",
    "disp",
    "displacement",
    "alpha",
    "opacity",
    "mask",
    "orm",
    "arm",
}


def texture_name_tokens(path: str) -> set[str]:
    """从贴图路径中提取小写语义 token。"""
    name = os.path.splitext(os.path.basename(path or ""))[0].lower()
    return {token for token in re.split(r"[^a-z0-9]+", name) if token}


def is_non_color_texture_path(path: str) -> bool:
    """判断路径是否明显指向非颜色贴图。"""
    tokens = texture_name_tokens(path)
    return any(token in _NON_COLOR_TEXTURE_TOKENS for token in tokens)


def should_use_as_color_texture(path: str) -> bool:
    """只有非空且不是已知非 color 语义时，才允许连接到材质 color。"""
    return bool(path) and not is_non_color_texture_path(path)
