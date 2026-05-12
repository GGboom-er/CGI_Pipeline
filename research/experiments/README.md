# research/experiments —— 实验脚本归档

这里存放 P0-A 时代留下的一次性实验脚本：CPD / 功能映射（Functional Map）/
FM benchmarking / Laplacian 扩散 / 体素化包裹等算法原型。

**⚠️ 已过期标签说明**

很多脚本里出现的动作级别是 P0-A 时代的老术语：

| P0-A 老标签        | 当前 compare 等价                  |
|-------------------|-----------------------------------|
| `PARTIAL_MATCH`   | `MODIFIED` / `MERGE` / `SPLIT`    |
| `REORDER`         | （已合并进 `MODIFIED` Step 3 分支）  |
| `AUTO_SAFE`       | `IDENTICAL` / `ORIG_INJECT`       |
| `SPATIAL_VOTING`  | 不再是 compare 的产出，sync skill 里用作 voting pool 的内部标签 |
| `SYNC_ORIG`       | `ORIG_INJECT`                     |

当前 compare 真相见 `core/asset_info_schema.py::compare()` 与 `CLAUDE.md`。

**保留这些脚本的原因**：算法推导过程、benchmark 数据、debug 可视化代码
在未来重写议题 2B 决策树时仍有参考价值。保留不维护。
