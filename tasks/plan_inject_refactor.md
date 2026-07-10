# 绑定替换资产 · 注入重构落地计划

> 目标：把「点+UV+法线全从 ABC 一次写进 orig 的 base」这套(前台实测验通)落进代码，替换现在坏的两趟注入 + 会清坏几何的 pnts 逻辑。

## 根因回顾（实测坐实）
- `_inject_points_via_datablock` 的 `MFnMesh.create` parent 传成 mesh shape → 直接炸、静默失败 → 点从没写进 base。
- 末尾清 pnts 假设「base 存全部几何」，但 hair(orig 自带 pnts)/眼球(几何靠上游 polySurfaceShape28 撑)base 是脏/空的 → 清 pnts 把真实几何清没 → hair 偏 0.75、眼球飞 130。

## 新机制（已验：眼球端到端 + hair 法线两路）
一份内存 MFnMeshData 承载 ABC 点+拓扑+UV+法线，一次写进 orig 的 `cachedInMesh` plug（无临时 DAG mesh）：
```
data = MFnMeshData().create()
MFnMesh().create(ABC点, ABC拓扑, parent=data)
setUVs + assignUVs(ABC UV)                        # 单套 map1
setFaceVertexNormals(ABC facevarying法线)         # 无则不设→Maya重算
origShape.cachedInMesh.setMObject(data)           # 一次写进 base
求值 getAttr(boundingBoxMin)
```

## 改动文件与阶段

### 阶段 1 · `core/abc_reader.py` — 加读 ABC 法线
- 完整模式(非 lightweight)在 entry(现 250 行 dict)新增读法线：`schema.getNormalsParam()` → `getExpandedValue()` 取 facevarying N（眼球 4192 / hair 29982）。
- entry 加字段：`normals`（[x,y,z,...] 展平）+ scope 标记。
- winding_flipped 时法线索引若需同步反转，一并处理（与 face_indices/uv_indices 同步）。
- signed_volume 那套**降级为兜底**：有 ABC 法线用 ABC 的，没有才走重算/相交判向。
- 验证：契约测试（有 N / 无 N 两例）。

### 阶段 2 · `maya_sync_rig_incremental.py` — 新注入函数
- 新增 `_inject_mesh_data_via_plug(orig_shape, abc_entry)`：内存 MFnMeshData 建 ABC 点+拓扑 → setUVs/assignUVs → setFaceVertexNormals(有则赋、无则跳过让 Maya 重算) → 写 `cachedInMesh` → 求值。
- 新增 `_clean_orig_upstream(orig_shape)`：顺 `inMesh` 找上游构造节点(polySurface/polySoftEdge 等**非变形器**)删除；**禁用 `cmds.delete(ch=True)`**（实测会连 skinCluster/blendShape 一起删）。通用识别、不硬编码节点名。
- 新增 `_regularize_uvset_to_map1(shape)`：真 shape 上 `polyUVSet delete` 删多余 + `rename` 成 map1，**必须在写 cachedInMesh 前**。
- 法线朝向校正 `_ensure_normals_outward(shape)`：用相交阈值（复用/参考 `_classify_layer_depth` 射线奇偶）——片状不管、管状/封闭件自相交 >20% 判反、翻。

### 阶段 3 · `maya_sync_rig_incremental.py` — 改 dispatch（1100-1151）
- IDENTICAL / ORIG_INJECT **合并**：都走「清上游 → uvSet 规整 map1 → `_inject_mesh_data_via_plug`(ABC 全量)」，不再有 inject_pts=None 的 IDENTICAL 空搬运。
- 删除 `_inject_points_via_datablock`(593)、`_inject_uv_via_sandbox`(634)、`_inject_abc_uv`(703) 三个旧函数及其调用(1144)。
- `_relocate_rig_mesh`(489)：保留 reparent/rename 层级搬运，注入段换成新函数。

### 阶段 4 · 清 pnts + workflow 编排
- 末尾清 pnts(1352 `_clear_pnts_tweak`)：改成**每件写完 base 后就地清 shape+orig**（顺序：先写 base、后清 pnts），保留但现在永远安全。
- workflow `tex_to_rig_verify_and_sync.json`：**去掉 step7 simplify_uvsets**（注入已收 map1）；step10 fix_shape_names 删「垃圾 orig」那半（并进注入清上游），保留改名。

## 保持不变
- 两次对比 step6/step12 **保留**（确认注入/重建有效，非冗余）。
- 材质：按 UV 象限(UDIM)赋，无 faceSet 对齐。
- reparent/rename 层级搬运、Phase4 新建件逻辑（新建件本就从 ABC create，同样受益于新法线）。

## 验证矩阵（改完必跑）
| 层 | 内容 |
|---|---|
| 单元 | abc_reader 读 N 契约（有 N/无 N）；sync_contract 不受影响回归 |
| 前台端到端 | 干净 v001 上跑改后 sync，验 hair1(自带pnts开放件)+眼球(上游历史双skin)+1普通件：点/UV/法线=ABC、uvSet=map1、进base、清pnts不塌、变形器吃、可见shape对、法线朝外 |
| 回归 | 整条 `tex_to_rig_verify_and_sync` 重跑 maYouD v001 → post-compare 三件从「塌」变「闭合」，blocking=0 |
| 清理 | 删诊断残留（DIAG_* 盒子、_diag/ 目录） |

## 风险 / 待确认
1. **点序**：不加校验（用户定：compare 过=点序一致）。
2. **无法线兜底**：Maya 重算 vs 自算法——阶段 1 先做「无则 Maya 重算 + 相交阈值判向」，够则不加额外算法。
3. **删旧函数**：`_inject_points_via_datablock` 等可能被别处引用——删前 grep 全库确认无其他调用。
4. **分阶段可回滚**：先 abc_reader（阶段1）独立可测；再 sync（2-4）。每阶段跑验证再进下一阶段。

## 执行顺序
阶段1(abc_reader+测) → 阶段2(新函数+单测) → 阶段3(dispatch 切换) → 阶段4(pnts+workflow) → 前台端到端 → 回归重跑。

