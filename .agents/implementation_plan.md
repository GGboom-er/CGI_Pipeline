# 拓扑无关模型绑定同步引擎 — 算法组合方案设计

> ⚠️ **历史规划文档（P0-A 时代）**
>
> 这是 P0-A 时期把 research 目录里 CPD / Functional Map / Laplacian
> 等算法落盘成工程实现的早期方案。里面提到的动作级别（PARTIAL_MATCH、
> REORDER 等）以及 `subset_mapping / free_vertices` 指令已在当前
> compare 引擎中合并/重命名。
>
> 真相以 `core/asset_info_schema.py` 与 `CLAUDE.md` 为准。保留本文
> 供回顾设计思路。

本方案旨在结合 `research` 目录下的高端学术算法，构建一条能够自动化抵御各类恶劣美术输入的"拓扑无关绑定迁移管线"。

## 一、 算法核心执行流 (The Pipeline Flow)

面对任何新旧模型交接任务，管线将严格按照以下 **5 个阶段** 依次串行执行。前一步为后一步提供更为优质的数学基础。

### 阶段 1：姿态对齐与免疫污染 (Pose Alignment & De-pollution)
**解决痛点：** 场景 7 (Pose 污染)
**选用库：** `pycpd` (或 `non_rigid_icp`)
**执行逻辑：**
1. 提取旧 Rig 模型和新 Asset 模型的纯顶点坐标云。
2. 运行 CPD (Coherent Point Drift) 算法，将旧模型视为“柔性体”，自动向新模型进行非刚性形变吸附（Non-Rigid Registration）。
3. **输出：** 一个在空间上与新资产完美贴合的“临时旧模型”。此后的所有计算（距离、法线等）均基于这个临时模型，从而彻底消除美术导出时手抖带入的微小 Pose 误差。

### 阶段 2：高维特征匹配与拓扑映射 (Robust Correspondence)
**解决痛点：** 场景 2 (点序全乱)、场景 3 (局部修剪/新增细节)
**选用库：** `pyFM` (Functional Maps) + `scipy.optimize.linear_sum_assignment` (匈牙利算法)
**执行逻辑：**
1. 放弃单纯看 XYZ 距离的 KDTree 贪心策略。
2. 如果点数一致但点序错乱：计算两者的距离代价矩阵，强行运行**匈牙利算法**求出全局唯一的最优二分图匹配映射。
3. 如果点数不一致：运行 `pyFM` 通过拉普拉斯-贝尔特拉米算子提取模型的“几何谐波特征”，在完全不依赖顶点序号的情况下，找到新旧模型上几何特征绝对吻合的**“精确锚点集”（Anchor Subset）**。
4. **输出：** 一份高置信度的点对点映射表（双射）。

### 阶段 3：蒙皮权重双调和扩散 (Weight Diffusion)
**解决痛点：** 场景 3 (无中生有的新细节过渡)
**选用库：** `robust_laplacian` + `potpourri3d`
**执行逻辑：**
1. 针对阶段 2 找到的“精确锚点集”，将旧模型的蒙皮权重以 100% 精度直接复制过去。
2. 针对新长出来的拓扑（如新增的口袋、加线的区域），将其视为未知自由点（Free Points）。
3. 调用 `robust_laplacian` 构建新模型的**余切拉普拉斯刚度矩阵 (Cotangent Laplacian)**。
4. 解**双调和偏微分方程 (BBW, $\Delta^2 W = 0$)**。将锚点集作为 Dirichlet 边界条件，让权重顺着模型的物理表面纹理自然扩散到自由点上。
5. **输出：** 保证 $C^1$ 级丝滑物理过渡的全新 SkinCluster 权重。

### 阶段 4：跨拓扑表情迁移 (BlendShape Deformation Transfer)
**解决痛点：** 拓扑大改后的表情与 BS 继承
**选用库：** `deformation_transfer` (Python/SciPy) 或 `deformation_transfer_cpp`
**执行逻辑：**

## 4. 纯空间映射求解器架构 (Pure Spatial Mapping Engine)

根据您的核心诉求，我们完全推翻之前的“找表面、打射线、算法滤除红点再平滑”的常规思路。
既然只有 `AA` 和 `HH` 两个数据，且要求解纯粹的**空间映射解**，我们将采用 **K-Nearest Neighbors Inverse Distance Weighting (KNN-IDW 空间反距离加权法)**。

### 4.1 数学机制 (Spatial Volume Logic)
不把 `AA` 和 `HH` 当作有皮的网格，而是把 `AA` 视作一个散布在 3D 空间中的**能量节点云**。
当 `HH` 的一个点 $Q$ 插入这个空间时，无论它悬空多高、有没有对齐：
1. **寻找邻域 (KNN)**：以 $Q$ 为球心，在三维空间中向外扩张，直到网罗住 `AA` 上最近的 $K$ 个点（例如 K=4 或 K=8）。这就如同在减面算法中，一个被坍塌的点去吸纳它周围保留下来的点。
2. **反距离加权 (IDW)**：这 $K$ 个点对 $Q$ 的影响力，严格与它们在三维空间中的距离平方成反比。
   公式：$Weight_i = \frac{1 / (d_i + \epsilon)^2}{\sum 1 / (d_k + \epsilon)^2}$
3. **求解映射**：$Q$ 的属性（无论是骨骼权重，还是表情位移），直接由这 $K$ 个点的属性按空间权重融合而成。

### 4.2 为什么这个底层函数才是“搞对了”？
*   **绝对不会失败 (No Unmatched)**：不需要法线一致，不需要距离阈值。哪怕 `HH` 长出一对角，角的尖端也能通过空间球形扩散，平滑继承头部基底的形变。
*   **极高鲁棒性 (Topology Immune)**：不管面朝哪边、不管面有多大，纯粹算空间物理坐标的相互引力。

## Proposed Changes

### `core/deformation_field.py`
#### [MODIFY] deformation_field.py
1. **废弃原本基于 `MMeshIntersector` 的射线投射逻辑**，这东西太依赖法线和多边形面。
2. **新增核心求解器 `_compute_spatial_mapping_knn(self, Q, K=4)`**：
   - 构建 `AA` 顶点的全局 `scipy.spatial.cKDTree`。
   - 对 `HH` 的所有点执行 `tree.query(Q, k=K)`。
   - 在内存中直接生成 $(N_{HH}, N_{AA})$ 的稀疏插值矩阵。
3. **重写 `sample_weights` 和 `sample_bs_deltas`**：
   - 使用求出的空间映射矩阵，直接执行光速矩阵乘法，秒出纯空间映射解。

## User Review Required

> [!IMPORTANT]
> **算法重构确认**
> 我已经把计划改为纯粹的 **空间映射 (KNN-IDW 体积场)** 求解逻辑。
> 这个算法只拿你给的 AA 和 HH 两个坐标点集，直接在 3D 空间中拉起引力网，计算点和点簇之间的空间距离关系，强行出解，**完全抛弃表面网格的束缚**。
> 如果同意这个底层函数的数学方向，我就开始改写 `deformation_field.py`，并直接把映射结果输出给你看！ Source-Target 对应约束。
3. 将旧模型的每一个 BlendShape Target 输入 `deformation_transfer` 引擎。
4. 引擎提取旧模型三角形的“形变梯度矩阵”，并把这股扭曲力精确套用到新模型（甚至高模）的对应三角形上。
5. **输出：** 零损耗、不拉扯的全新 BlendShape Targets。

### 阶段 5：兜底空间包裹与防穿插拦截 (Spatial Wrap & Fallback)
**解决痛点：** 场景 6 (眼睫毛/眼皮极度贴合)、场景 4 (两鞋合并、拆分)
**选用方案（原生增强）：** 法线级联投射过滤 + 3D 体积坐标插值
**执行逻辑：**
1. 针对阶段 2 和 3 都无法处理的彻底重构的资产区块（如两个模型合二为一）。
2. 发射双向射线进行空间投射。**最关键：** 取交点时必须进行法线夹角检测，夹角 > 90 度（如上睫毛投射到了朝下的下眼皮）直接判死刑丢弃，强迫射线打透去找正确的匹配面。
3. 对找到的合法面，使用重心坐标系插值获取兜底权重。

---

## 二、 实际落地研发路线图

这套方案架构极其强大，但也异常复杂，建议按以下顺序在管线中逐步整合激活：

- [x] **Milestone 1 (快速见效)：实现「精确映射与双调和扩散」**
  - 在 `core/` 编写基于 `scipy.sparse` 和 `robust_laplacian` 的扩散解算器。
  - 修改 `asset_info_schema.py` 引入匈牙利算法替换 KDTree，解决点序打乱和局部细节平滑问题。
- [x] **Milestone 2 (消除人为误差)：集成「非刚性配准」**
  - 在同步动作开始前，插入一个利用 `pycpd` 的预处理节点，纠正微小 Pose 偏移。
- [ ] **Milestone 2.5 (空间映射求解)：实现「局部匹配与拆分/合并提取」(Tier 3 & Tier 4)**
  - 升级 `asset_info_schema.py`，突破“点数不一致即降级”的限制。利用 KDTree `query_ball_point` 和双射验证，找出两边精确匹配的**顶点子集 (Subset)**。
  - 对于新增/删减拓扑，输出明确的 `PARTIAL_MATCH` 指令并附带局部索引映射表，提取出游离点 (Free Points)。
  - 针对多对一、一对多，输出精确的 `MERGE` / `SPLIT` 分片指令。
- [ ] **Milestone 3 (彻底无视拓扑)：集成「Deformation Transfer」**
  - 利用现成的 `deformation_transfer` 代码，替代管线现有的 BS 点对点强拷逻辑，实现跨拓扑的高级表情迁移。

## 三、 需要您确认的几个问题

> [!IMPORTANT]
> **关于 Milestone 2.5 的核心攻坚确认：**
> 正如您所言，如果我们无法在 `compare` 引擎里把局部点到点的对应关系（谁跟谁是严丝合缝的，谁是新长出来的部分）明确地提炼成数组输出，后端的拉普拉斯扩散和表情迁移就成了无米之炊。
>
> 接下来我计划重点改造 `asset_info_schema.py` 中的 `_judge_action` 和 `_compare_positions`：
> 1. 不再仅仅因为点数不同就放弃，而是去计算**最大重合子集**。
> 2. 将最终给到后端的指令拓展为 `PARTIAL_MATCH`（包含旧点对新点的 `subset_mapping` 数组，以及需要被扩散算法处理的 `free_vertices` 列表）。
> 
> 您看这是您希望的“先求解空间映射对应关系”的思路吗？如果是，我们下一步就全力攻克 Milestone 2.5 的代码逻辑。

1. **计算性能考量**：`pycpd` 和双调和矩阵求解在超高精度（如 10 万面以上）下可能会计算数分钟甚至更久，目前管线是允许这种重型离线后台异步解算的对吗？
2. **C++ 与 Python 的取舍**：像 `deformation_transfer_cpp` 和 `cvwrap` 是带 C++ / Maya 插件编译的。为了跨平台部署方便，初期先用纯 Python / NumPy 版本的库（如纯 python 版的 `deformation_transfer`）去实现跑通逻辑，后续遇到性能瓶颈再换 C++ 版本，您看可以接受吗？
3. **优先级确认**：在 M1, M2, M3 中，结合目前生产中最常崩溃的环节，您倾向于优先开始敲定哪一个 Milestone 的代码实现？

---

## 四、 R3DS Wrap4D 工业级映射与 BS 传递方案解析

根据您的要求，我深入检索了工业界标杆 R3DS Wrap (Wrap4D) 的底层传递方法论，并与我们当前的管线进行对标。

### 1. Wrap4D 的核心传递逻辑 (DeltaTransfer)
Wrap4D 处理拓扑不一致和 BlendShape 传递时，从来不使用简单的“点对点吸附”或单纯的“线性位移 (Linear Displacement)”，而是基于 **形变梯度 (Deformation Gradient / Affine Transform)**。
* **光流与非刚性包裹 (Wrapping)：** 首先使用类似 CPD/ICP 的算法将标准拓扑（Base）非刚性包裹到目标扫描拓扑上，解决大比例差异（比如大鼻子转小鼻子）。
* **局部坐标系重建 (Local Frame Reconstruction)：** 在传递 BlendShape 时，Wrap 计算原网格每个三角形的法线和两边构成的 3x3 矩阵（局部切线空间）。当网格发生表情形变时，提取这个矩阵的旋转和缩放变化。
* **梯度施加 (Gradient Transfer)：** 将这个旋转和缩放矩阵，施加到新拓扑的对应三角形上。这保证了目标网格在继承表情时，能维持正确的**体积感和旋转弧度**（例如眼皮闭合时的眼球包裹感），彻底杜绝线性位移导致的拉丝和体积坍缩。

### 2. 我们管线 (`core/deformation_field.py`) 的现状与对标
我查阅了您当前管线代码中的 `DeformationField`，发现我们的架构**已经具备了追平 Wrap4D 的潜力**：
* **目前我用来可视化的精度**：刚刚我们在 Maya 里画线的精度，相当于 Wrap4D 里的 Point-to-Surface Projection（用 `MMeshIntersector` 求绝对表面重心坐标）。
* **目前管线里的 `_wrap_deform` 函数**：我们的代码里已经手写了基于 M_base 和 M_target 局部坐标系重建的逻辑！这本质上就是 Wrap4D `DeltaTransfer` 的平替（仿射空间传递）。

### 3. 我们相较于 Wrap4D 还缺失什么？（下一步升级点）
尽管我们有了 `_wrap_deform` 的数学基础，但要达到 Wrap4D 的效果，还需补齐以下环节：
1. **缺失的前置包裹 (Non-Rigid Registration)**：我们的 `pycpd` 目前是剥离状态。如果在投影重心坐标之前，不把源模型先“吸附”变形到目标模型的轮廓上，会导致不同比例的角色之间提取到的面片权重错位（比如下巴的权重提取到了脖子上）。
2. **多尺度平滑 (Subset Masks)**：Wrap4D 拥有基于表面拉伸的顶点遮罩（StretchVertexMask）。我们的拉普拉斯平滑（Laplacian Smooth）在跨拓扑边界处还需要引入类似的面元拉伸惩罚，防止 BS 传递后表面产生碎褶。

> [!TIP]
> **落地建议：** 我们不需要购买 Wrap4D，只需在 `DeformationField.sample_bs_deltas` 中，完全激活我们的 `_wrap_deform`，并在其外层强制包裹一层快速的 CPD 算法（或者 Laplacian Deformation）作为预对齐，即可复刻 Wrap4D 的核心传递质量！
