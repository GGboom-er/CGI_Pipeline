"""任务终态常量集中定义。

所有 poll 轮询逻辑（poll_task、_read_audit 消费者、巡航测试脚本）都从这里取，
避免散布多处导致 CHAIN_AUDIT_FAILED 这类新增状态被漏认。
"""


# Step / chain 层的单点终态（没有后续状态变更）
TERMINAL_STATUSES: frozenset[str] = frozenset({
    'PARTIAL',
    'SUBMIT_FAILED',
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
    'WORKFLOW_SUCCESS',
    'WORKFLOW_ABORTED',
    'WORKFLOW_ERROR',
    'WORKFLOW_AUDIT_FAILED',
    'CHAIN_NEEDS_ATTENTION',
    'WORKFLOW_NEEDS_ATTENTION',
    'SUCCESS_WITH_WARNINGS',
    'WORKFLOW_SUCCESS_WITH_WARNINGS',
    'CANCELLED',
    'TIMEOUT',
})


# 明确成功的状态
SUCCESS_STATUSES: frozenset[str] = frozenset({
    'SUCCESS',
    'CHAIN_SUCCESS',
    'WORKFLOW_SUCCESS',
    'SUCCESS_WITH_WARNINGS',
    'WORKFLOW_SUCCESS_WITH_WARNINGS',
})


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_success(status: str) -> bool:
    return status in SUCCESS_STATUSES


# Transport failure classification does not change polling terminal states.
# Attention is terminal but requires a caller decision, not automatic failure.
FAILURE_STATUSES: frozenset[str] = (
    TERMINAL_STATUSES - SUCCESS_STATUSES
    - {'PARTIAL', 'NEEDS_ATTENTION', 'STEP_NEEDS_ATTENTION', 'CHAIN_NEEDS_ATTENTION', 'WORKFLOW_NEEDS_ATTENTION'}
) | frozenset({'FAIL', 'FAILED', 'FAILURE', 'SUBMIT_FAILED'})


def is_failure(status: object) -> bool:
    """Classify an overall receipt without inferring from nested step statuses."""
    return isinstance(status, str) and status in FAILURE_STATUSES


def business_status(result: dict) -> str:
    """Keep a transport failure; refine successful envelopes using the API receipt."""
    import json
    from cgi_pipeline.contracts import API_STATUSES

    outer = result.get('status', 'ERROR')
    if outer not in SUCCESS_STATUSES:
        return outer if outer in TERMINAL_STATUSES else 'ERROR'
    receipt = result.get('receipt', result.get('detail'))
    if isinstance(receipt, str):
        try:
            receipt = json.loads(receipt)
        except ValueError:
            receipt = None
    if isinstance(receipt, dict) and receipt.get('api_id') and receipt.get('status') in API_STATUSES:
        return receipt['status']
    return outer
