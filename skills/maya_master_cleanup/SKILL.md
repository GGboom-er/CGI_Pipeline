---
skill_id: "maya_master_cleanup"
name: "管线全自动清理与优化"
dcc: "maya"
description: "基于管线项目上下文，扫描或清理未知节点、插件残留、野生相机、多余UV集、死动画帧与空骨骼权重。支持 Check（只读诊断）和 Fix（执行清理）两种模式。"
parameters:
  mode:
    type: "string"
    default: "check"
    description: "操作模式：'check' 生成质检报告（只读安全），'fix' 会产生破坏性修复动作"
  save_path:
    type: "string"
    default: ""
    description: "另存路径，清理后保存到此路径，MD报告自动输出在旁边"
io:
  inputs:
    - name: "scene"
      type: "scene_file"
      label: "Maya 场景"
  outputs:
    - name: "scene"
      type: "scene_file"
      label: "清理后场景"
    - name: "report"
      type: "report"
      label: "清理报告 (.md)"
category: "process"
---

### 🔴 核心限制 (CRITICAL CONSTRAINTS)
- **强破坏性执行**: 在 `fix` 模式下，工具将无情绞杀场景中所有冗余资源。这包括：无链接材质、空组、失效引用、未识别垃圾节点以及多重废弃 UV 集，**绝不可逆**。
- **环境隔离校验**: 保存路径被 `path_guard` 接管，只能保存于非保护区（如本地沙盒或发布缓存），如果强行替换源资产，脚本将阻断。

### 🟢 核心逻辑 (CORE LOGIC)
- 加载 `mode` (check/fix) -> 启动三层架构（管线特有诊断、高精细垃圾打捞、引擎内置兜底优化） -> 清扫 `unknown` 类型 -> 清扫空转变换节点 `transform` -> 清扫废料材质与垃圾显示层 -> 将执行耗时及扫除规模撰写为 MD 报告 -> `fix` 模式下转存到目的地。

### 🔵 核心代码与扩展 (IMPLEMENTATION & EXTENSION)
- **底层驱动**: 原生 `cmds.delete`, 配合引擎优化 API `mel.eval("cleanUpScene 1")`。
- **智能诊断模式**: `check` 模式采用了对等只读检索架构，它会返回待杀列表供用户前端查阅，保证人工 QC 先行，而不发生任何实质破坏。
- **可拓展控制**: 对于特定动画项目（如：必须保留空骨骼控制器），可直接在核心代码中扩展 `empty_group` 白名单过滤机制（例如检测是否包含自定义属性或者约束）。

### 🟡 参数规则 (PARAMETERS)
- `mode` (string): 选填 | `check` | 指定要执行的具体模式，填 `fix` 时将真刀真枪开展大扫除。
- `save_path` (string): 选填 | 无 | 清理后如果选择了 `fix`，保存的另存为绝对路径。
