---
skill_id: "maya_capture_viewport"
name: "Maya 视口截图"
dcc: "maya"
tier: "read"
pairs_with: []
description: "获取 Maya 当前活动视口的截图，并保存为 PNG。"
parameters: {}
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
利用 Win32 PrintWindow API 抓取 Maya 当前前台窗口的完整截图（PNG格式），返回图片的本地绝对路径。
当系统需要 AI 检查视口状态、模型外观、BlendShape 形变时调用此技能。

### 🔵 核心代码与扩展 (IMPLEMENTATION)
1. 在 `runs/captures/` 目录下创建一个具有唯一时间戳的 PNG 文件。
2. 使用 Win32 PrintWindow (PW_RENDERFULLCONTENT) 从 DWM 合成器抓取完整窗口内容，包含 OpenGL/DirectX 渲染。
3. 截图分辨率由窗口实际尺寸决定，不需要参数指定。

### 🟡 参数规则 (PARAMETERS)
无用户参数。截图分辨率由 Maya 窗口实际尺寸决定。

### 🟣 标准执行记录 (RECORD)
- `output.output_path` (str): 截图 PNG 文件路径。
