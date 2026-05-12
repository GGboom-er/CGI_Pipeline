# core/schemas.py
# 核心数据流 Schema 验证 (Fail-Fast 防御层)

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

class SkillPayloadSchema(BaseModel):
    """
    通用技能执行的负载校验模型。
    在提交到 Celery 队列时或刚进入 Worker 内存时立即进行验证，
    避免字典隐式报错延迟到 DCC API 调用层。
    """
    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    skill_id: Optional[str] = Field(default=None, description="要执行的技能 ID (对于链式执行为可选)")
    project: str = Field(..., description="项目代号", min_length=1)
    asset_name: str = Field(..., description="资产名称", min_length=1)
    source_path: str = Field(default="", description="Maya/Blender 场景文件路径，可以为空")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="技能专属参数，透传给具体的技能执行体")

    from pydantic import model_validator
    @model_validator(mode='after')
    def validate_skill_parameters(self):
        if not self.skill_id:
            return self
            
        from core.skill_registry import get_skill_map
        skill_map = get_skill_map()
        skill_info = skill_map.get(self.skill_id)
        if not skill_info:
            return self
            
        defined_params = skill_info.get('parameters', {})
        
        # 框架路由参数白名单：由 MCP 层注入，到达 Worker 时自动剥离，不视为脏参数
        _FRAMEWORK_PARAMS = {'execution_mode', 'foreground_port'}
        for fp in _FRAMEWORK_PARAMS:
            self.parameters.pop(fp, None)
        
        # 1. 拦截未定义的参数 (Reject Extra)
        extra_keys = set(self.parameters.keys()) - set(defined_params.keys())
        if extra_keys:
            raise ValueError(f"技能 '{self.skill_id}' 拒绝未定义的脏参数: {extra_keys}。请查阅 SKILL.md 规范。")
            
        # 2. 类型检查与默认值回填
        for key, config in defined_params.items():
            if key not in self.parameters:
                if 'default' in config:
                    self.parameters[key] = config['default']
                else:
                    raise ValueError(f"技能 '{self.skill_id}' 缺失必填参数: '{key}'")
            else:
                val = self.parameters[key]
                expected_type = config.get('type', 'string')
                if expected_type == 'string' and not isinstance(val, str):
                    raise ValueError(f"参数 '{key}' 期望 string, 收到 {type(val).__name__}")
                elif expected_type in ('number', 'float') and not isinstance(val, (int, float)):
                    raise ValueError(f"参数 '{key}' 期望 number, 收到 {type(val).__name__}")
                elif expected_type in ('boolean', 'bool') and not isinstance(val, bool):
                    raise ValueError(f"参数 '{key}' 期望 boolean, 收到 {type(val).__name__}")
                elif expected_type in ('array', 'list') and not isinstance(val, list):
                    raise ValueError(f"参数 '{key}' 期望 array, 收到 {type(val).__name__}")
                elif expected_type in ('object', 'dict') and not isinstance(val, dict):
                    raise ValueError(f"参数 '{key}' 期望 object, 收到 {type(val).__name__}")
        
        return self

class SkillChainPayloadSchema(BaseModel):
    """
    链式执行的负载校验模型。
    """
    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    task_id: str = Field(..., description="任务 ID")
    source_path: str = Field(..., description="要处理的场景文件路径", min_length=1)
    project: str = Field(default="default", description="项目代号")
    asset_name: str = Field(default="untitled", description="资产名称")
    category: str = Field(default="", description="资产分类")
    skill_chain: List[Dict[str, Any]] = Field(..., description="技能链的执行节点列表", min_length=1)

    from pydantic import model_validator
    @model_validator(mode='after')
    def validate_chain_parameters(self):
        from core.skill_registry import get_skill_map
        skill_map = get_skill_map()
        
        for i, step in enumerate(self.skill_chain):
            skill_id = step.get('skill_id')
            if not skill_id:
                raise ValueError(f"链式执行的第 {i} 步缺少 skill_id")
                
            skill_info = skill_map.get(skill_id)
            if not skill_info:
                continue
                
            defined_params = skill_info.get('parameters', {})
            step_params = step.get('parameters', {})
            
            # 1. 拦截未定义的参数
            extra_keys = set(step_params.keys()) - set(defined_params.keys())
            if extra_keys:
                raise ValueError(f"链式执行 step {i} ('{skill_id}') 拒绝脏参数: {extra_keys}。")
                
            # 2. 类型检查与默认值
            for key, config in defined_params.items():
                if key not in step_params:
                    if 'default' in config:
                        step_params[key] = config['default']
                    else:
                        raise ValueError(f"链式执行 step {i} ('{skill_id}') 缺失必填参数: '{key}'")
                else:
                    val = step_params[key]
                    expected_type = config.get('type', 'string')
                    if expected_type == 'string' and not isinstance(val, str):
                        raise ValueError(f"参数 '{key}' 期望 string, 收到 {type(val).__name__}")
                    elif expected_type in ('number', 'float') and not isinstance(val, (int, float)):
                        raise ValueError(f"参数 '{key}' 期望 number, 收到 {type(val).__name__}")
                    elif expected_type in ('boolean', 'bool') and not isinstance(val, bool):
                        raise ValueError(f"参数 '{key}' 期望 boolean, 收到 {type(val).__name__}")
                    elif expected_type in ('array', 'list') and not isinstance(val, list):
                        raise ValueError(f"参数 '{key}' 期望 array, 收到 {type(val).__name__}")
                    elif expected_type in ('object', 'dict') and not isinstance(val, dict):
                        raise ValueError(f"参数 '{key}' 期望 object, 收到 {type(val).__name__}")
                        
            # Update the validated dictionary back to the step
            step['parameters'] = step_params
            
        return self
