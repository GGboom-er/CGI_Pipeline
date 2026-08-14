# tools 目录使用准则

`tools/` 只放 catalog 生成、Notes 同步、治理检查和通用辅助脚本；原子 DCC 能力必须放在 `src/cgi_pipeline/capabilities/`，不在这里维护第二套执行入口。

## 当前规则

- `build_catalogs.py`：校验 capability/package 真相源并生成 `src/cgi_pipeline/data/*.json`。
- `check_pipeline_governance.py`：检查生成物漂移、handler、workflow 引用和 MCP 固定入口。
- `sync_notes_api_index.py`：读取派生 catalog，生成 Notes 项目层能力目录并刷新 CGI 项目卡摘要。
- `smoke_mcp.py`：对真实 MCP endpoint 调用 `list_apis`，验证数量与专业字段。
- cdfBaiXingG 形变继承实验脚本归档到 `tools/archive/deformation_inheritance_cdfbaixingG_20260516/`。
- 归档脚本只用于追溯历史结论，不作为当前实现依据。
- 新一轮 `M_Head_base -> A` 权重复刻必须从 `projects/ysj/20260513_193837_cdfbaixingG/test.ma` 重新导出数据，不能复用旧 body2 correspondence map。

## 新增能力

如果继续产品化 Skin 权重复刻，应新增稳定 API，而不是继续在根目录追加临时脚本。稳定入口需要做到：

```text
输入场景
→ source mesh
→ target mesh
→ 重新构建当前 target 专用 correspondence
→ 低置信诊断
→ 受约束权重反求
→ Maya actual graph 验收
```
