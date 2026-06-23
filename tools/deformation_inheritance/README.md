# 当前形变继承主线脚本

本目录只放当前可复跑的形变继承实验入口。历史 cdfBaiXingG v0xx 脚本已归档到：

```text
tools/archive/deformation_inheritance_cdfbaixingG_20260516/
tools/archive/deformation_inheritance_reboot_20260517/legacy_scripts/
```

当前基准：

```text
Y:\GGbommer\scripts\CGI_Pipeline\projects\ysj\20260513_193837_cdfbaixingG\test.ma
source: M_Head_base
target: A
task: Skin 权重复刻
```

原则：

- 每一轮都从 `test.ma` 导出当前数据。
- 禁止复用 body2 的旧 correspondence map。
- 禁止继续沿用 v078-v081 对照场景作为算法基线。
- 先只读验证 source-target mesh/权重对应关系，再写候选权重。
- v082 开始必须先生成 correspondence validator 报告，报告不过不进入权重求解。
- 新脚本按阶段命名，例如 `maya_export_mhead_to_a_v082_data.py`、`validate_mhead_to_a_v082_correspondence.py`、`solve_mhead_to_a_v082_weights.py`。
