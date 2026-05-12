# Research Libraries — 空间网格映射与变形转移

克隆日期: 2026-05-07

## 目录

| 文件夹 | 来源 | 用途 |
|--------|------|------|
| `deformation_transfer/` | [mickare/Deformation-Transfer-for-Triangle-Meshes](https://github.com/mickare/Deformation-Transfer-for-Triangle-Meshes) | 跨拓扑 BS 迁移核心引擎 (Python + SciPy) |
| `deformation_transfer_cpp/` | [chand81/Deformation-Transfer](https://github.com/chand81/Deformation-Transfer) | 同上的 C++ Maya 节点版 (Eigen) |
| `pyFM/` | [RobinMagnet/pyFM](https://github.com/RobinMagnet/pyFM) | Functional Maps 谱域对应关系求解 |
| `smooth_functional_maps/` | [RobinMagnet/SmoothFunctionalMaps](https://github.com/RobinMagnet/SmoothFunctionalMaps) | Dirichlet 能量正则化的平滑 FM |
| `reversible_harmonic_maps/` | [RobinMagnet/ReversibleHarmonicMaps](https://github.com/RobinMagnet/ReversibleHarmonicMaps) | 双射调和映射 (GPU 加速) |
| `probreg/` | [neka-nat/probreg](https://github.com/neka-nat/probreg) | 概率配准算法集 (CPD/BCPD/FilterReg) |
| `pycpd/` | [siavashk/pycpd](https://github.com/siavashk/pycpd) | Coherent Point Drift 纯 NumPy 实现 |
| `non_rigid_icp/` | [shubhamag/non_rigid_icp](https://github.com/shubhamag/non_rigid_icp) | Amberg NICP 非刚性配准 |
| `potpourri3d/` | [nmwsharp/potpourri3d](https://github.com/nmwsharp/potpourri3d) | 测地距离 / Heat Method / 向量热方程 |
| `robust_laplacian/` | [nmwsharp/robust-laplacians-py](https://github.com/nmwsharp/robust-laplacians-py) | 鲁棒正定拉普拉斯算子 |
| `diffumatch/` | [daidedou/diffumatch](https://github.com/daidedou/diffumatch) | 谱域扩散先验零样本形状匹配 (ICCV 2025) |
| `non_rigid_shape_correspondence/` | [ZHLRJ/Non-Rigid-Shape-Correspondence](https://github.com/ZHLRJ/Non-Rigid-Shape-Correspondence) | Karcher Mean 无监督 landmark 发现 |
| `cvwrap/` | [chadmv/cvwrap](https://github.com/chadmv/cvwrap) | C++ 局部坐标系重心映射包裹 (GPU 加速) |
| `snarf/` | [xuchen-ethz/snarf](https://github.com/xuchen-ethz/snarf) | 神经隐式蒙皮 (Neural Implicit Skinning) |
| `pytorch3d/` | [facebookresearch/pytorch3d](https://github.com/facebookresearch/pytorch3d) | Meta 3D 深度学习与可微渲染基础库 |

## 优先级

1. **立即可用**: `deformation_transfer`, `robust_laplacian`, `potpourri3d`, `pycpd`
2. **需要 GPU/PyTorch**: `diffumatch`, `reversible_harmonic_maps`, `probreg` (CUDA 加速), `snarf`, `pytorch3d`
3. **参考学习**: `pyFM`, `smooth_functional_maps`, `non_rigid_shape_correspondence`, `cvwrap` (源码研读)
4. **需要编译 Maya SDK**: `deformation_transfer_cpp`, `cvwrap` (如需在 Maya 运行)

## pip 可直接安装的

```bash
pip install robust-laplacian potpourri3d pyfmaps pycpd probreg
```
