# mcp_server/models.py
# ── Pydantic Input Models ──
# 从 server.py 拆分：所有 MCP Tool 的输入校验模型

from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict, create_model


# ══════════════════════════════════════════════════
# 基类
# ══════════════════════════════════════════════════

class _SkillInput(BaseModel):
    """通用技能提交参数"""
    model_config = ConfigDict(str_strip_whitespace=True)
    project: str = Field(..., description="项目代号，如 'ysj'。用于加载项目配置和路径规则", min_length=1, max_length=50)
    asset_name: str = Field(..., description="资产名称，如 'xiaotianquan'。与 category 组合定位资产目录", min_length=1, max_length=100)
    source_path: str = Field(default="", description="Maya 场景文件路径（.ma/.mb）。建议先用 maya_resolve_asset 获取。为空则操作当前已打开的场景")
    execution_mode: str = Field(default="background", description="执行模式：background(后台无头) 或 foreground(当前界面)")
    foreground_port: Optional[int] = Field(default=None, description="Foreground port. REQUIRED when execution_mode='foreground'. Get it from maya_list_foreground_sessions first. Do NOT hardcode. Ignored in background mode. 前台端口，foreground 模式必须显式从 maya_list_foreground_sessions 获取后传入。")


# ══════════════════════════════════════════════════
# 动态技能参数生成
# ══════════════════════════════════════════════════

def create_skill_model(skill_info: dict) -> type[BaseModel]:
    """根据技能字典（注册表）动态生成 Pydantic Input Model"""
    fields = {}
    skill_id = skill_info.get('skill_id', 'unknown_skill')
    
    # 基础字段 project/asset_name/source_path/execution_mode/foreground_port 从 _SkillInput 继承
    # （见下方 create_model 的 __base__）；此处 fields 只收 skill 专属动态参数，单一真相源
    
    # 动态参数
    parameters = skill_info.get('parameters', {})
    for p_name, p_def in parameters.items():
        p_type_str = p_def.get('type', 'string')
        if p_type_str in ('float', 'number'):
            p_type = float
        elif p_type_str in ('int', 'integer'):
            p_type = int
        elif p_type_str in ('bool', 'boolean'):
            p_type = bool
        elif p_type_str in ('list', 'array'):
            p_type = list
        elif p_type_str in ('dict', 'object'):
            p_type = dict
        else:
            p_type = str
        
        desc = p_def.get('description', '')
        if 'default' in p_def:
            fields[p_name] = (p_type, Field(default=p_def['default'], description=desc))
        else:
            fields[p_name] = (p_type, Field(..., description=desc))
            
    # 类名构造: 'maya_clean_skinweights' -> 'MayaCleanSkinweightsInput'
    model_name = ''.join(word.capitalize() for word in skill_id.split('_')) + 'Input'
    
    # 基础字段继承自 _SkillInput（单一真相源，含 foreground_port 等框架字段）
    return create_model(model_name, __base__=_SkillInput, **fields)


# ══════════════════════════════════════════════════
# 代码执行
# ══════════════════════════════════════════════════

class ExecCodeInput(BaseModel):
    """在 Maya 中执行任意 Python 代码"""
    model_config = ConfigDict(str_strip_whitespace=True)
    code: str = Field(..., description="要在 Maya 中执行的 Python 代码。必须将结果赋值给 result 变量（dict），如 result = {'status': 'SUCCESS', 'data': ...}", min_length=1)
    description: str = Field(default="", description="代码用途描述，写入审计日志便于追溯")
    project: str = Field(default="default", description="项目代号")
    asset_name: str = Field(default="untitled", description="资产名称")
    source_path: str = Field(default="", description="执行前先打开此场景文件（可选）")
    execution_mode: str = Field(default="background", description="执行模式：background(后台无头) 或 foreground(当前界面)")
    foreground_port: Optional[int] = Field(default=None, description="Foreground port. REQUIRED when execution_mode='foreground'. Get it from maya_list_foreground_sessions first. Do NOT hardcode. Ignored in background mode. 前台端口，foreground 模式必须显式从 maya_list_foreground_sessions 获取后传入。")
    sync: bool = Field(default=True, description="仅 foreground 有效：True（默认）直接同步等 RPyC 返回并在响应里带 receipt，省掉 query_task 轮询（亚秒级响应）。传 False 恢复异步行为。")


# ══════════════════════════════════════════════════
# 链式执行
# ══════════════════════════════════════════════════

class ChainStep(BaseModel):
    """链式执行的单个步骤"""
    model_config = ConfigDict()
    skill_id: str = Field(..., description="技能 ID，如 'clean_skinweights'")
    parameters: dict = Field(default_factory=dict, description="技能专属参数")

class ExecuteChainInput(BaseModel):
    """链式执行参数"""
    model_config = ConfigDict(str_strip_whitespace=True)
    source_path: str = Field(..., description="要处理的 Maya 场景文件路径。链引擎会自动打开此文件，步骤中不需要再传 source_path", min_length=1)
    project: str = Field(default="default", description="项目代号")
    asset_name: str = Field(default="untitled", description="资产名称")
    skill_chain: list[ChainStep] = Field(..., description="按顺序执行的技能步骤列表。save_scene 必须放最后一步", min_length=1)
    execution_mode: str = Field(default="background", description="执行模式：background(后台无头) 或 foreground(当前界面)")
    foreground_port: Optional[int] = Field(default=None, description="Foreground port. REQUIRED when execution_mode='foreground'. Get it from maya_list_foreground_sessions first. Do NOT hardcode. Ignored in background mode. 前台端口，foreground 模式必须显式从 maya_list_foreground_sessions 获取后传入。")


# ══════════════════════════════════════════════════
# 查询类
# ══════════════════════════════════════════════════

class TaskQueryInput(BaseModel):
    """任务查询参数"""
    model_config = ConfigDict(str_strip_whitespace=True)
    task_id: str = Field(..., description="任务 ID，格式为 task-xxxx 或 chain-xxxx", min_length=1)

class ResolveAssetInput(BaseModel):
    """资产查询参数"""
    model_config = ConfigDict(str_strip_whitespace=True)
    project: str = Field(..., description="项目代号，如 'ysj'", min_length=1)
    asset_name: str = Field(..., description="资产名称，如 'xiaotianquan'", min_length=1)
    category: str = Field(default="chr", description="资产分类，从项目配置 categories 中选取（如 chr/prp/env）")
    stage: str = Field(default="", description="阶段，从项目配置 stages 中选取（如 mod/uv/tex/rig/lyrig/lib）。为空则按环节优先级自动选择")
    task: str = Field(default="", description="任务名（可选）。为空则使用该阶段的 primary_task（如 rigMaster）。传入具体值可定位 sub task")
    pipeline: str = Field(default="", description="制作环节（可选）。model=模型环节(tex>uv>mod)，rig=绑定环节(lib>rig>lyrig)。不传 stage 时生效，限定查找范围")

class ResolveShotInput(BaseModel):
    """镜头查询参数"""
    model_config = ConfigDict(str_strip_whitespace=True)
    project: str = Field(..., description="项目代号，如 'ysj'", min_length=1)
    sequence: str = Field(..., description="场次名称，如 'sc01'", min_length=1)
    shot: str = Field(..., description="镜头名称，如 'cam001'", min_length=1)
    stage: str = Field(default="", description="阶段（如 ly/ani/cfx/efx/lgt/mat）。为空则返回所有可用阶段")
    task: str = Field(default="", description="任务名（可选）。为空则使用该阶段的 primary_task")


# ══════════════════════════════════════════════════
# 通用 / 平台级
# ══════════════════════════════════════════════════

class ExecuteSkillInput(BaseModel):
    """通用技能执行参数（兜底接口，优先使用具名 Tool）"""
    model_config = ConfigDict(str_strip_whitespace=True)
    skill_id: str = Field(..., description="技能 ID，用 maya_list_skills 查看可用值。有具名 Tool 的技能请直接用具名 Tool", min_length=1)
    project: str = Field(..., description="项目代号", min_length=1)
    asset_name: str = Field(..., description="资产名称", min_length=1)
    source_path: str = Field(default="", description="源文件路径，建议先用 maya_resolve_asset 获取")
    parameters: Optional[dict] = Field(default=None, description="技能专属参数，用 maya_list_skills 查看每个技能的参数 Schema")
    execution_mode: str = Field(default="background", description="执行模式：background(后台无头) 或 foreground(当前界面)")
    foreground_port: Optional[int] = Field(default=None, description="Foreground port. REQUIRED when execution_mode='foreground'. Get it from maya_list_foreground_sessions first. Do NOT hardcode. Ignored in background mode. 前台端口，foreground 模式必须显式从 maya_list_foreground_sessions 获取后传入。")

class StartWorkerInput(BaseModel):
    """启动 DCC Worker 参数"""
    model_config = ConfigDict()
    dcc: str = Field(default="maya", description="要启动或重启的 Worker 类型：maya / blender / workflow / pipeline")

class CopyFilesInput(BaseModel):
    """文件拷贝参数"""
    model_config = ConfigDict(str_strip_whitespace=True)
    source: str = Field(..., description="源路径（文件或目录的完整路径）", min_length=1)
    destination: str = Field(..., description="目标路径（文件或目录的完整路径）", min_length=1)
    overwrite: bool = Field(default=False, description="目标已存在时是否覆盖")

class CompareAssetInput(BaseModel):
    """资产对比（独立数据入口）

    任意两份 _info.json / .abc 全量几何对比。DCC 源文件必须先通过
    采集或导出技能转换为标准数据文件。
    """
    model_config = ConfigDict(str_strip_whitespace=True)
    project: str = Field(default="default", description="项目代号，用于创建任务沙盒")
    asset_name: str = Field(default="compare", description="资产名，用于创建任务沙盒")
    input_source: str = Field(..., description="source 侧 _info.json 或 .abc 路径", min_length=1)
    input_target: str = Field(..., description="target 侧 _info.json 或 .abc 路径", min_length=1)
    label_source: str = Field(default="", description="source 标签，从路径自动推断阶段: rig/tex/uv/model/anim/fx/cfx/look")
    label_target: str = Field(default="", description="target 标签，从路径自动推断阶段: rig/tex/uv/model/anim/fx/cfx/look")

class SyncRigAssetInput(BaseModel):
    """同步资产到绑定文件参数"""
    model_config = ConfigDict(str_strip_whitespace=True)
    project: str = Field(default="default", description="项目代号")
    asset_name: str = Field(default="untitled", description="资产名称")
    source_path: str = Field(..., description="target 侧 rig 场景路径（Celery 框架约定键名，本 skill 里是被修改的目标场景）")
    compare_result: Any = Field(default="", description="前置对比生成的 compare_result.json 路径，或 workflow 上游传入的 output.compare_result 字典。拼装必须依据该结果执行")
    source_abc: str = Field(default="", description="source 侧 ABC 路径，推荐。能重建 NEW mesh")
    source_info: str = Field(default="", description="source 侧 _info.json 路径（无 ABC 时的降级路径）")
    cache_group: str = Field(default="cache", description="target rig 几何根组，由项目配置传入")
    dry_run: bool = Field(default=False, description="兼容参数。只看差异请使用 maya_compare_asset_in_scene")

class ExecuteWorkflowInput(BaseModel):
    """工作流执行参数"""
    model_config = ConfigDict(str_strip_whitespace=True)
    workflow_id: str = Field(..., description="工作流 ID，用 list_workflows 查看可用值", min_length=1)
    source_path: str = Field(default="", description="可选源文件路径（Blender .blend 或 Maya .ma/.mb）。为空时由 workflow 内的解析节点按 asset_name 查找")
    project: str = Field(default="default", description="项目代号")
    asset_name: str = Field(default="untitled", description="资产名称")
    extra_params: Optional[dict] = Field(default=None, description="工作流额外参数，用于模板变量 {{input.xxx}} 替换")
