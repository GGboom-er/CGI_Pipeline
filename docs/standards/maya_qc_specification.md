# Maya 场景质检（QC）规范手册

> 源码路径：`app/_maya/qc/`
> 基类：`ppas.app.base.qc_tool.qc.util.ModelCheck`
> 框架约定：每个检查项暴露 `get_qc_obj()` 工厂函数，返回检查类

---

## 一、场景清理（Clean）— 28 项

> 路径：`qc/clean/`
> 性质：**破坏性操作**，直接删除/修改节点
> `qc_type: 'clean'`

### 1.1 动画相关（3 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 1 | `ani_curve` | 清理动画曲线 | ❌ | 遍历所有 `animCurve` 节点，跳过引用节点和驱动关键帧，解锁后删除 |
| 2 | `ani_layer` | 清理动画层 | ✅ | 删除所有 `animLayer` 节点；删除失败的记录为 error，提供手动删除回调 |
| 3 | `script_job` | 清理 ScriptJob | ❌ | `cmds.scriptJob(ka=1)` 全部杀掉（实际标记为 `qc_type: 'optimize'`） |

### 1.2 渲染/显示层（4 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 4 | `display_layer` | 清理显示层 | ✅ | 删除非 `defaultLayer` 的显示层；**ly/ani 阶段保留 `hide` 层**。删除失败的提供手动回调 |
| 5 | `render_layer` | 清理渲染层 | ✅ | 删除非 `defaultRenderLayer` 的渲染层；删除失败的提供手动回调 |
| 6 | `turtle_layer` | 清理海龟渲染层 | ❌ | 删除所有 `ilrBakeLayer` 类型节点（Turtle 插件残留） |
| 7 | `aov` | 清理 AOV | ✅ | 删除所有 `aiAOV` + `RedshiftAOV` 节点；删除失败的提供手动回调 |

### 1.3 灯光相关（4 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 8 | `light` | 清理灯光 | ❌ | 删除 `defaultLightSet` 中的所有灯光节点 |
| 9 | `light_editor` | 清理灯光编辑器 | ❌ | 删除所有 `lightEditor` 节点（会在场景内叠加导致卡顿） |
| 10 | `light_connections_linker` | 清理灯光链接 | ❌ | 断开 `initialShadingGroup.msg` 的非标连接 + 断开 `lightLinker1` 的所有源连接 |
| 11 | `env_light` | 清理环境光节点 | ❌ | 删除 10 种环境光类型：`RedshiftEnvironment/PhysicalSky/PhotoGraphicExposure/Bokeh/LensDistortion/VolumeScattering` + `aiPhysicalSky/RaySwitch/Fog/VolumeScattering` |

### 1.4 相机（1 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 12 | `delete_camera` | 清理多余相机 | ❌ | 保留 `persp/top/front/side` 四个默认相机 + 投射相机（连接 `projection` 节点的）；其余全部删除，含关联的 `lookAt` 节点 |

### 1.5 材质/Shader（2 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 13 | `shader` | 清理无用材质 | ❌ | 调用 MEL `hyperShadePanelMenuCommand("hyperShadePanel1", "deleteUnusedNodes")` |
| 14 | `useless_shader` | 清理无用材质球 | ❌ | 删除所有 `unknown` 类型节点（⚠️ 代码实际与 unknown_node 重复） |

### 1.6 未知节点/插件/引用（3 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 15 | `unknown_node` | 清理未知节点 | ❌ | 删除所有 `type='unknown'` 的节点（否则无法存储为 .ma） |
| 16 | `unknown_plugIn` | 清理未知插件 | ❌ | Maya 2017+ 调用 `cmds.unknownPlugin(plu, r=1)` 移除无用插件注册 |
| 17 | `unknown_reference` | 清理未知引用 | ❌ | 查找所有 `reference` 节点，`referenceQuery` 失败的视为失效引用并删除 |

### 1.7 约束/表达式（2 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 18 | `constraint` | 清理无用约束 | ❌ | 遍历 9 种约束类型（point/aim/orient/parent/scale/normal/tangent/geometry/pointOnPoly），删除**无下游连接**的约束 |
| 19 | `expression` | 清理无用表达式 | ❌ | 删除 `output` 属性无下游连接的 `expression` 节点 |

### 1.8 数据清理（4 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 20 | `blind_data_template` | 清理盲数据模板 | ❌ | 删除所有 `blindDataTemplate` 节点（跟随文件叠加导致膨胀） |
| 21 | `poly_blind_data` | 清理多边形盲数据 | ❌ | 删除所有 `polyBlindData` 节点（减少文件大小） |
| 22 | `data_structure` | 清理数据结构 | ❌ | `cmds.dataStructure(ral=True)` 移除所有数据结构（累积导致卡顿） |
| 23 | `point_on_curve_info` | 清理 PointOnCurveInfo | ❌ | 删除所有 `pointOnCurveInfo` 节点 |

### 1.9 层级/组织（3 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 24 | `transform` | 清理空组 | ❌ | OpenMaya 2.0 遍历所有 transform，递归删除无子节点且无有效连接（排除 displayLayer/renderLayer/animLayer 连接）的空组 |
| 25 | `namespace` | 清理命名空间 | ❌ | OpenMaya 2.0 循环移除所有命名空间（保留 `:UI` 和 `:shared`），`mnr=1` 合并到根 |
| 26 | `sets` | 清理无用 Sets | ❌ | 删除**空的** objectSet（保留 `defaultLightSet/defaultObjectSet/initialParticleSE/initialShadingGroup`） |

### 1.10 安全相关（2 项）

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 27 | `script` | 清理 Script 节点 | ❌ | 删除所有 `script` 节点（保留 `MakeTSM3ControlsMenu`）+ **杀掉 `leukocyte.antivirus()` 恶意 scriptJob** |
| 28 | `script_job` | 清理 ScriptJob | ❌ | 见 1.1 第 3 项 |

---

## 二、模型检查（Mod）— 9 项

> 路径：`qc/mod/`
> 性质：**诊断为主**，部分支持自动修复
> `qc_type: 'check'` 或 `'optimize'`

| # | 模块 | 检查项 | auto_fix | tip | 实现逻辑 |
|---|---|---|---|---|---|
| 1 | `duplicate_name` | 物体重名检查 | ✅ | error | OpenMaya 2.0 遍历所有 DAG 节点，按短名分组，重名项自动递增编号修正 |
| 2 | `default_name` | 默认名称检查 | ❌ | error | 正则匹配 `pCube\d+/pSphere\d+/group\d+/joint\d+` 等 17 种默认命名模式，列出需手动改名的物体 |
| 3 | `freeze_mesh` | 未冻结物体检查 | ✅ | error | OpenMaya 2.0 检查所有 transform 的 T/R/S 是否归零（容差 0.01）；排除投射相机、流体 Shape、灯光、GPU 缓存、`hightLightPointGrp` 组。修复调用 `makeIdentity` |
| 4 | `mutil_shape` | 多 Shape 节点检查 | ✅ | error | 检查每个 transform 下非 intermediateObject 的 mesh shape 数量是否 > 1；**chr/prp 类型额外清理 `\|Group\|cache` 下非标准命名的 Shape**。修复时保留第一个非中间体 shape |
| 5 | `wmrs_mutil_shape` | WMRS 多 Shape 检查 | ✅ | error | 与 `mutil_shape` 逻辑相同但**不执行 `clean_orig`**（无项目类型判断） |
| 6 | `lambert_was_used` | lambert1 材质占用 | ❌ | error | `cmds.hyperShade(o='lambert1')` 选中所有使用 lambert1 的物体并报告 |
| 7 | `polygon_problem` | 模型点线面问题 | ❌ | none | 调用 MEL `polyCleanupArgList 4 {...}` 检测非法几何（允许部分角色豁免名单） |
| 8 | `pre_fix_name` | 物体前缀不统一 | ✅ | error | 从文件名解析资产名，递归检查 `cache` 组下所有物体前缀是否匹配；env 类型跳过；修复自动替换前缀 |
| 9 | `conform_mesh` | 修正法线 + 点位移 | ❌ | none | 对所有 mesh 执行 `polyNormal(normalMode=2, userNormalMode=0, ch=0)` 统一法线方向（`qc_type: 'optimize'`） |

---

## 三、UV 检查 — 7 项（+ 1 工具模块）

> 路径：`qc/uv/`
> 性质：**诊断为主**，重度依赖 `uv_util.UvEditor` 工具类
> `qc_type: 'check'`

| # | 模块 | 检查项 | auto_fix | 实现逻辑 |
|---|---|---|---|---|
| 1 | `incorrect_uv_set_name` | UV Set 名称不正确 | ❌ | `UvEditor.incorrectName()` 检查当前 UV Set 名是否为 `map1` |
| 2 | `multi_grid_uvs` | UV 跨象限 | ❌ | `UvEditor.multiGridUvs()` 检查单个面的 UV 是否跨越 UDIM 象限边界 |
| 3 | `multi_uv_set` | 多个 UV Set | ❌ | OpenMaya 2.0 检查 `numUVSets > 1`（排除 `PencilSelectedEdge*` 开头的 set）。修复时删除非 `map1` 的 UV Set |
| 4 | `negative_uv` | 负空间 UV | ❌ | `UvEditor.negativeUvs()` 检查 UV 坐标是否在负空间（U < 0 或 V < 0） |
| 5 | `overlapped_uv` | UV 自重叠 | ❌ | `UvEditor.overlappedUvs()` 检查同一 UV Set 内 UV 面是否重叠（标记为 warning） |
| 6 | `reverse_uv` | 反向 UV | ✅ | `polySelectConstraint(ufo=2)` 选取反向 UV 面；修复用 `polyFlipUV` 翻转。仅检查 `cache` 组下的 mesh |
| 7 | `uv_map1` | 当前 UV Set 不是 map1 | ❌ | `UvEditor.unMap1()` 检查当前活动 UV Set 是否为 `map1` |

> **注意**：UV 检查模块内含 `wk` 项目 `wukong` 资产的特殊豁免逻辑

**工具模块**：`uv_util.py`（9.7KB）封装了 `UvEditor` 类，提供所有 UV 检查的底层算法实现

---

## 四、绑定 QC（Rig）— 10 项

> 路径：`qc/rig/`
> 性质：**绑定阶段专用**，混合诊断与修复

| # | 模块 | 检查项 | qc_type | auto_fix | 实现逻辑 |
|---|---|---|---|---|---|
| 1 | `analyse_scene_materials` | 物料贴图分析 | — | — | 非标准 QC 类。扫描场景 `file/RedshiftNormalMap/RedshiftSprite` 节点，收集贴图路径（支持 UDIM 扩展），输出材质报表 |
| 2 | `cache_channel` | 通道栏检查 | check | ❌ | 检查 cache 组下物体的 translate/rotate/scale 是否归零（T=0, R=0, S=1） |
| 3 | `check_tex_mesh_info` | Tex Mesh 信息变化 | check | ❌ | 对比当前场景 mesh 信息与 tex 阶段发布的 JSON 记录，检测顶点数/面数/材质等是否发生变化 |
| 4 | `fix_orig_shape` | 修复 Orig 节点命名 | optimize | ❌ | 修复绑定文件中蒙皮物体的 `ShapeOrig` 节点命名规范 |
| 5 | `rig_material` | 检查绑定贴图 | check | ❌ | 检查绑定文件内的贴图路径是否合法，与 tex 阶段数据校验一致性 |
| 6 | `rig_rename_shape` | 修复 Shape 命名 | optimize | ❌ | 修复绑定文件中模型 Shape 节点的命名规范（确保 `{transform}Shape` 格式） |
| 7 | `unused_skin_deformation` | 清理无用蒙皮节点 | clean | ❌ | 清理场景内所有无用的 `skinCluster` 变形器 |
| 8 | `vis_data_check` | 检查 vis_data 是否存在 | check | ✅ | 检查场景内是否存在 `vis_data` 节点（用于存储模型显隐动画信息，确保缓存进入其他流程后显隐不丢失） |
| 9 | `vis_data_attrs` | vis_data 属性合法性 | check | ❌ | 检查 `vis_data` 节点上的属性是否合法（属性名与 cache 组 mesh 一致） |
| 10 | `vis_data_until` | vis_data 工具库 | — | — | 非 QC 类。8.8KB 工具模块，封装 vis_data 节点的创建/读取/写入/校验方法 |

---

## 五、QC 框架架构

### 5.1 类继承体系

```
util.ModelCheck (基类)
├── virtual_check_content()  ← 子类必须实现
├── _results: List[ModelError]  ← 错误收集器
└── _is_warning: bool  ← 是否为警告级别

util.ModelError (错误对象)
├── set_error_node_name()     ← 问题节点名
├── set_error_node_msg()      ← 错误描述模板
├── set_responsible_nodes()   ← 关联节点（用于 UI 选中定位）
└── set_adjustment(func, *args)  ← 修复回调函数
```

### 5.2 注册元数据（`qc_infos` 字典）

每个检查项必须声明 `qc_infos` 字典：

```python
qc_infos = {
    'auto_fix': bool,       # 是否提供一键修复
    'command': str,         # 执行命令类名
    'description': str,     # 详细描述（支持 \n 换行）
    'label': str,           # UI 显示标签
    'module': str,          # 完整模块路径
    'name': str,            # 唯一标识 '{category}.{id}'
    'qc_type': str,         # check | clean | optimize
    'tip_type': str,        # none | warning | error
}
```

### 5.3 执行流程

```
QC 工具面板
  → 加载 qc_infos 注册表
  → 用户点击执行
  → 实例化 CheckClass()
  → 调用 virtual_check_content()
  → 收集 _results 列表
  → UI 展示错误项
  → 用户点击修复 → 调用 adjustment 回调
```

---

## 六、阶段适用矩阵

| 检查类别 | mod | uv | tex | rig | lib | ly | ani | lgt |
|---|---|---|---|---|---|---|---|---|
| 场景清理 28 项 | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ |
| 模型检查 9 项 | ✅ | ✅ | — | — | — | — | — | — |
| UV 检查 7 项 | — | ✅ | ✅ | — | — | — | — | — |
| 绑定 QC 10 项 | — | — | — | ✅ | ✅ | — | — | — |

> ⚠️ ly/ani/lgt 阶段的清理项有特殊豁免（如 display_layer 保留 `hide` 层）

---

## 七、已知问题与改进建议

| 问题 | 位置 | 建议 |
|---|---|---|
| `useless_shader.py` 实际删除 `unknown` 节点，与 `unknown_node.py` 完全重复 | `clean/useless_shader.py` | 重构为真正的无用材质球清理 |
| `env_light.py` 类名为 `ConstraintCheck`，与 `constraint.py` 冲突 | `clean/env_light.py` | 重命名为 `EnvLightCheck` |
| 多处重复的 `get_all_dag_nodes` 静态方法 | `mod/*.py` | 提取到 `util` 基类或公共工具模块 |
| UV 检查硬编码了 `wk/wukong` 项目豁免 | `uv/*.py` | 改为配置驱动的豁免列表 |
| `polygon_problem.py` 硬编码了角色豁免名单 | `mod/polygon_problem.py` | 改为项目配置 |
| `script_job.py` 标记为 `qc_type: 'optimize'` 但放在 `clean/` 目录 | `clean/script_job.py` | 统一分类标准 |
