import time
from pathlib import Path

from core.receipt import make_receipt

def execute(payload: dict) -> dict:
    """Meta-skill 占位执行入口。

    真正的规则在同目录 SKILL.md。当前 receipt 底层仍处于迁移期，
    这里返回兼容形态，但只暴露规则文件路径，不再注入旧式长正文。
    """
    t0 = time.time()
    protocol_path = str(Path(__file__).with_name("SKILL.md"))
    
    return make_receipt(
        skill_id='build_pipeline_skill',
        status='SUCCESS',
        start_time=t0,
        summary_input='build_pipeline_skill',
        summary_action='返回唯一 skill 构建规则路径',
        summary_count=1,
        summary_label='规则',
        outputs={'output_path': protocol_path},
    )
