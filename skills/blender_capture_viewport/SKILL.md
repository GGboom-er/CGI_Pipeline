---
skill_id: "blender_capture_viewport"
name: "Blender 视口截图"
dcc: "blender"
description: "获取 Blender 当前活动视口或后台渲染截图，并保存为 PNG。"
parameters:
  width:
    type: "number"
    default: 1920
    description: "截图宽度"
  height:
    type: "number"
    default: 1080
    description: "截图高度"
io:
  inputs:
    - name: "scene"
      type: "blend_file"
      label: "Blender 场景"
  outputs:
    - name: "output_path"
      type: "image_file"
      label: "PNG 截图"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
后台模式可能没有真实 OpenGL 视口上下文，失败时必须返回 ERROR receipt，不能静默忽略。

### 🟢 核心功能 (CORE FUNCTION)
利用 `bpy.ops.render.opengl` 抓取 Blender 视口的图像（PNG格式），返回图片的本地绝对路径。
当系统需要 AI 检查 Blender 视口状态、模型外观、材质渲染时调用此技能。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
1. 在 `runs/captures/` 目录下创建一个具有唯一时间戳的 PNG 文件路径。
2. 临时修改场景渲染参数（尺寸、格式），并执行 `bpy.ops.render.opengl(write_still=True)`。
3. 恢复原始渲染参数。
4. 若截图成功，返回图片的完整绝对路径。

### 🟡 参数规则 (PARAMETERS)
- `width` (number): 截图宽度，默认 1920。
- `height` (number): 截图高度，默认 1080。
