# tools 目录使用准则

⚠️ **历史混合目录 · 归档规则**

本目录 `tools/` 含通用脚本 + 历史脚本。

**主线目录：**
- `scripts/` - 独立CLI工具
- `skills/` - MCP可调用能力

---

`tools/` 根目录只放当前仍可作为入口使用的通用辅助脚本。资产专项、一次性诊断、v0xx 参数回归脚本必须放到归档子目录，避免后续把历史验证脚本误当成当前主线。

## 当前规则

- `mcp_exec_code_file.py`、`mcp_foreground_probe.py`、`verify_mcp_contract.py` 这类通用 MCP / 调试入口可以留在根目录。
- `skill_metadata_audit.py`：检查所有 `SKILL.md` 的 `tier` / `pairs_with` 字段是否完整、取值是否合法、关联 skill 是否存在。
- `sync_notes_skill_index.py`：读取 `python cli.py list-skills --json`，生成 Notes 项目层 `ai/projects/cgi_pipeline_skill_catalog.md` 并刷新 `ai/projects/cgi_pipeline.md` 的 skill 工具摘要。
- cdfBaiXingG 形变继承实验脚本归档到 `tools/archive/deformation_inheritance_cdfbaixingG_20260516/`。
- 归档脚本只用于追溯历史结论，不作为当前实现依据。
- 新一轮 `M_Head_base -> A` 权重复刻必须从 `projects/ysj/20260513_193837_cdfbaixingG/test.ma` 重新导出数据，不能复用旧 body2 correspondence map。

## 后续落地

如果继续产品化 Skin 权重复刻，应新增稳定入口或 skill，而不是继续在根目录追加 `v0xx` 临时脚本。稳定入口需要做到：

```text
输入场景
→ source mesh
→ target mesh
→ 重新构建当前 target 专用 correspondence
→ 低置信诊断
→ 受约束权重反求
→ Maya actual graph 验收
```
