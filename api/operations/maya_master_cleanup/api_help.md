# API Help: maya.asset.maya_master_cleanup

- 作用与场景：基于管线项目上下文，扫描或清理未知节点、插件残留、野生相机、多余UV集、死动画帧与空骨骼权重。支持 Check（只读诊断）和 Fix（执行清理）两种模式。
- 用法：`execute_api(maya.asset.maya_master_cleanup, params={...})`
- DCC：`maya`；层级：`destructive`；执行：`background / foreground`

## 参数
- `mode`（默认 `check`）：操作模式：'check' 生成质检报告（只读安全），'fix' 会产生破坏性修复动作
- `save_path`（默认 ``）：另存路径，清理后保存到此路径，MD报告自动输出在旁边

## 输出
- 基于管线项目上下文，扫描或清理未知节点、插件残留、野生相机、多余UV集、死动画帧与空骨骼权重。支持 Check（只读诊断）和 Fix（执行清理）两种模式。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
