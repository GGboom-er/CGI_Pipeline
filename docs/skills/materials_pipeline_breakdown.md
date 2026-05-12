# 材质采集与应用 skill 拆解

更新时间: 2026-05-12

本文档按运行时总规范 v1 拆解材质链路的职责、输入、输出、已确认决策和本轮落地结果。

涉及 skill：

- `blender_extract_materials`
- `maya_apply_materials`

## 1. 定位

材质链路负责把 Blender source 的面级材质事实传给 Maya target。

`blender_extract_materials`：

- 从 Blender 沙盒场景读取面级材质分配
- 采集颜色、透明度、贴图路径
- 将 UDIM 按象限拆分为独立材质条目
- 输出 `_materials.json`

`maya_apply_materials`：

- 消费 `_materials.json`
- 在 Maya 中创建 lambert 材质球、SG、file 节点
- 按 `faces_by_mesh` 对 mesh 面赋材

## 2. 职责边界

不是 `blender_extract_materials` 的职责：

- ABC 导出
- `_info.json` 几何采集
- Maya 材质创建
- 报告落盘

不是 `maya_apply_materials` 的职责：

- Blender 材质采集
- source 贴图扫描推断
- 几何对比
- ABC 构建

Maya 无来源信息时的 UV 象限推断仍归 `maya_assign_udim_materials` / `maya_split_udim_materials`。

## 3. 输入契约

### 3.1 blender_extract_materials parameters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `output_path` | string | workflow 必填 | `_materials.json` 输出路径，必须位于 `{sandbox}/.info/` |
| `cache_group` | string | 是 | Blender 几何根对象，由项目配置或 workflow 传入 |

推荐 workflow 写法：

```json
{
  "step_id": "extract_materials",
  "skill_id": "blender_extract_materials",
  "parameters": {
    "output_path": "{{input.info_dir}}/{{input.source_path | stem}}_materials.json",
    "cache_group": "{{config.stages.tex.geom_roots.0}}"
  }
}
```

### 3.2 maya_apply_materials parameters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `materials_path` | string | 是 | 上游 `_materials.json` 路径 |
| `target_group` | string | 否 | 限定处理范围的 Maya 组；为空处理全场景 |

推荐 workflow 写法：

```json
{
  "step_id": "apply_materials",
  "skill_id": "maya_apply_materials",
  "parameters": {
    "materials_path": "{{outputs.extract_materials.output_path}}",
    "target_group": "{{config.stages.rig.geom_roots.0}}"
  }
}
```

## 4. 输出契约

`blender_extract_materials` 文件产物：

```text
{sandbox}/.info/{source_stem}_materials.json
```

receipt.outputs：

```json
{
  "output_path": "..._materials.json"
}
```

`maya_apply_materials` 修改当前 Maya 沙盒场景，不产出文件路径。

## 5. `_materials.json` 数据内容

```json
{
  "materials": {
    "MatName_1001": {
      "color": {"type": "texture", "path": "...", "is_udim": false},
      "alpha": {"type": "value", "value": 1.0, "semantic": "alpha"},
      "faces_by_mesh": {"cache|grp|mesh": [0, 1, 2]}
    }
  }
}
```

## 6. 已确认决策

- `_materials.json` 是机器中间产物，必须写入 `.info`。
- workflow 必须显式传 `output_path` 和 `cache_group`。
- 单技能兜底只从 `info_dir` / `run_dir` / `task_id` 推导 `.info`。
- 无法推导沙盒 `.info` 时返回 `ERROR`。
- 不允许回退到源 `.blend` 同目录。
- `blender_export_abc` 不再隐式生成 `_materials.json`。

## 7. 本轮代码落地

已更新：

- `skills/blender_extract_materials/blender_extract_materials.py`
- `skills/blender_extract_materials/SKILL.md`
- `tools/verify_mcp_contract.py`

行为变化：

- 未传 `output_path` 时不再回退源文件同目录。
- `cache_group` 缺失直接返回 `ERROR`。
- 受保护输出路径直接返回 `BLOCKED`。
- 静态门禁阻断旧 `_materials.json` fallback 回归。

## 8. 验证点

静态验证：

- workflow 中 `blender_extract_materials.output_path` 包含 `{{input.info_dir}}`
- `output_path` 文件名包含 `_materials.json`
- `cache_group` 来自 `{{config...}}`
- 代码中不存在源目录 fallback

运行验证：

- 显式 `output_path` 时输出到指定 `.info`
- 仅传 `info_dir` 时自动输出到 `.info`
- 无 `output_path/info_dir/run_dir/task_id` 时返回 `ERROR`
- `maya_apply_materials` 能消费该 JSON

## 9. 拆解结论

材质采集与应用链路已拆清：Blender 只生产 `_materials.json`，Maya 只消费并赋材。
