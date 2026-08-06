# API Help: pipeline.pipeline.pipeline_compare_asset

- 作用与场景：纯 JSON/ABC 数据对比。三步漏斗（路径→等点数→空间）配对，输出 source 侧每个 mesh 相对 target 的 4 种事实归属 + 算法层 7 标签细分。
- 用法：`execute_api(pipeline.pipeline.pipeline_compare_asset, params={...})`
- DCC：`pipeline`；层级：`read`；执行：`background`

## 参数
- `input_source`（默认 ``）：source 侧 _info.json 或 .abc 路径
- `input_target`（默认 ``）：target 侧 _info.json 或 .abc 路径
- `label_source`（默认 ``）：source 标签（自动推断: rig/tex/uv/model/anim 等）
- `label_target`（默认 ``）：target 标签
- `output_path`（默认 ``）：compare_result.json 输出路径。workflow/单API调用必须传入任务沙盒 .info 路径或可推导 info_dir。

## 输出
- 纯 JSON/ABC 数据对比。三步漏斗（路径→等点数→空间）配对，输出 source 侧每个 mesh 相对 target 的 4 种事实归属 + 算法层 7 标签细分。

此文件是渐进式阅读入口；机器契约以同目录 `api.yaml` 为准。
