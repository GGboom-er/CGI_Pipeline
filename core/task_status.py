"""任务终态常量集中定义。

所有 poll 轮询逻辑（poll_task、_read_audit 消费者、巡航测试脚本）都从这里取，
避免散布多处导致 CHAIN_AUDIT_FAILED 这类新增状态被漏认。
"""


# Step / chain 层的单点终态（没有后续状态变更）
TERMINAL_STATUSES: frozenset[str] = frozenset({
    'SUCCESS',
    'ERROR',
    'BLOCKED',
    'AUDIT_FAILED',
    'STEP_ERROR',
    'STEP_BLOCKED',
    'NEEDS_ATTENTION',
    'STEP_NEEDS_ATTENTION',
    'STEP_AUDIT_FAILED',
    'CHAIN_SUCCESS',
    'CHAIN_ABORTED',
    'CHAIN_ERROR',
    'CHAIN_BLOCKED',
    'CHAIN_AUDIT_FAILED',
    'CHAIN_HELD',
    'WORKFLOW_SUCCESS',
    'WORKFLOW_ABORTED',
    'WORKFLOW_ERROR',
    'WORKFLOW_AUDIT_FAILED',
    'CANCELLED',
    'TIMEOUT',
})


# 旧版人工暂停状态，仅供历史 audit/调试兼容。后台 pipeline 主路径不再产生这些状态。
HELD_STATUSES: frozenset[str] = frozenset({
    'NEEDS_ATTENTION',
    'STEP_NEEDS_ATTENTION',
    'CHAIN_HELD',
})


# 明确成功的状态
SUCCESS_STATUSES: frozenset[str] = frozenset({
    'SUCCESS',
    'CHAIN_SUCCESS',
    'WORKFLOW_SUCCESS',
})


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_held(status: str) -> bool:
    return status in HELD_STATUSES


def is_success(status: str) -> bool:
    return status in SUCCESS_STATUSES
