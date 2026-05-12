---
skill_id: "maya_capture_viewport"
name: "Maya 视口截图"
dcc: "maya"
description: "获取 Maya 当前活动视口的截图，并保存为 PNG。"
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
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "output_path"
      type: "image_file"
      label: "PNG 截图"
category: "inspect"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
主要用于前台 Maya 窗口。后台无界面时可能无法获得有效窗口句柄，必须返回 ERROR receipt。

### 🟢 核心功能 (CORE FUNCTION)
利用 `cmds.playblast` 抓取 Maya 当前前台活动视口的图像（PNG格式），返回图片的本地绝对路径。
当系统需要 AI 检查视口状态、模型外观、BlendShape 形变时调用此技能。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
1. 在 `projects/temp_capture/` 目录下创建一个具有唯一时间戳的 PNG 文件。
2. 调用 `cmds.playblast(format="image", compression="png", completeFilename=temp_path, forceOverwrite=True, viewer=False, widthHeight=(width, height), percent=100, frame=cmds.currentTime(query=True))`。
3. 若截图成功，返回图片的完整绝对路径。

### 🟡 参数规则 (PARAMETERS)
- `width` (number): 截图宽度，默认 1920。
- `height` (number): 截图高度，默认 1080。
