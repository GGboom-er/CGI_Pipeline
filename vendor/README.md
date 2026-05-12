# vendor/ — 第三方依赖打包目录

本目录包含 CGI Pipeline 运行所需的第三方 Python 库，随项目一起发布。
DCC（Maya/Blender）启动时由 `core/bootstrap.py` 自动注入 `sys.path`，无需手动安装。

## 当前包含

| 包 | 版本 | 用途 |
|---|---|---|
| trimesh | 4.12.2 | v7 面级投影：构建三角化 mesh 并执行 on_surface 最近点查询 |
| rtree | 1.4.1 | trimesh 的空间索引后端（R-Tree 加速近邻搜索） |

## 注意

- **不要在此目录放 numpy/scipy**：DCC 自带的版本优先生效（`sys.path.append` 保证低优先级）
- 更新依赖：`mayapy -m pip install <pkg> --target vendor/ --upgrade --no-user`，然后删除 vendor/numpy*
