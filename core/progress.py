# core/progress.py
# ── 统一进度推送模块 — Redis Pub/Sub + REST 回退 ──
#
# Worker 侧调用 publish_event() 推送进度事件到 Redis channel，
# 同时写入 Redis hash 供 REST 端点做断线重连回退查询。
#
# 事件格式标准（对齐 AYON Event System 设计理念）：
# {
#   "workflow_id": "wf-xxx",
#   "event_type": "step.start",     // workflow.start | segment.start | step.start | step.done | ...
#   "segment": 3,
#   "step": 1,
#   "step_total": 4,
#   "skill_id": "maya_fix_shape_names",
#   "progress": 0.25,               // 0.0 ~ 1.0
#   "message": "正在规范化 Shape 命名...",
#   "timestamp": 1777723900.123
# }

import json
import time
import logging

logger = logging.getLogger(__name__)

_redis = None


def _get_redis():
    """延迟获取 Redis 连接（单例）"""
    global _redis
    if _redis is None:
        try:
            import redis
            _redis = redis.Redis(
                host='127.0.0.1', port=6379, db=0,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            _redis.ping()
        except Exception as e:
            logger.warning(f'Redis 连接失败，进度推送降级为仅审计文件: {e}')
            _redis = None
    return _redis


# ═══════════════════════════════════════════════════════════════
# 事件推送（Worker 侧调用）
# ═══════════════════════════════════════════════════════════════

def publish_event(workflow_id: str, event: dict):
    """
    推送进度事件到 Redis Pub/Sub channel + 写入最新状态 hash。

    参数:
        workflow_id: 工作流 ID（如 wf-0ff021d8）
        event: 事件字典，必须包含 event_type 字段
    """
    if not workflow_id:
        return

    event.setdefault('workflow_id', workflow_id)
    event.setdefault('timestamp', time.time())

    r = _get_redis()
    if r is None:
        return  # Redis 不可用时静默降级

    try:
        channel = f"wf:{workflow_id}"
        event_json = json.dumps(event, default=str, ensure_ascii=False)

        # 1. Pub/Sub 实时推送（SSE 端点订阅此 channel）
        r.publish(channel, event_json)

        # 2. Hash 写入最新状态（REST 回退查询用）
        state_key = f"wf:{workflow_id}:state"
        r.hset(state_key, mapping={
            'latest_event': event_json,
            'event_type': event.get('event_type', ''),
            'progress': str(event.get('progress', 0)),
            'message': event.get('message', ''),
            'updated_at': str(time.time()),
        })
        r.expire(state_key, 86400)  # 24 小时 TTL
    except Exception as e:
        logger.warning(f'进度事件推送失败: {e}')


# ═══════════════════════════════════════════════════════════════
# 状态查询（API 侧调用）
# ═══════════════════════════════════════════════════════════════

def get_latest_state(workflow_id: str) -> dict:
    """
    REST 回退：从 Redis hash 获取最新进度状态。

    前端断线重连时调用，获取最新快照后重新订阅 SSE。
    """
    r = _get_redis()
    if r is None:
        return {}

    try:
        state_key = f"wf:{workflow_id}:state"
        raw = r.hget(state_key, 'latest_event')
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════
# 段间 Outputs 持久化（断点恢复用）
# ═══════════════════════════════════════════════════════════════

def persist_outputs(workflow_id: str, all_outputs: dict):
    """将段间传递的 outputs 字典持久化到 Redis，支持断点恢复。"""
    r = _get_redis()
    if r is None:
        return

    try:
        key = f"workflow:{workflow_id}:outputs"
        r.set(key, json.dumps(all_outputs, default=str, ensure_ascii=False))
        r.expire(key, 86400)  # 24 小时 TTL
    except Exception as e:
        logger.warning(f'outputs 持久化失败: {e}')


def restore_outputs(workflow_id: str) -> dict:
    """从 Redis 恢复段间 outputs（断点续跑时调用）。"""
    r = _get_redis()
    if r is None:
        return {}

    try:
        key = f"workflow:{workflow_id}:outputs"
        raw = r.get(key)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def mark_segment_done(workflow_id: str, seg_idx: int):
    """标记某段已完成（断点恢复时用于跳过已完成段）。"""
    r = _get_redis()
    if r is None:
        return

    try:
        key = f"workflow:{workflow_id}:completed_segments"
        r.sadd(key, str(seg_idx))
        r.expire(key, 86400)
    except Exception as e:
        logger.warning(f'段完成标记失败: {e}')


def get_completed_segments(workflow_id: str) -> set:
    """获取已完成段索引集合。"""
    r = _get_redis()
    if r is None:
        return set()

    try:
        key = f"workflow:{workflow_id}:completed_segments"
        members = r.smembers(key)
        return {int(m) for m in members} if members else set()
    except Exception:
        return set()


def cleanup_workflow_state(workflow_id: str):
    """工作流完成后清理 Redis 中的临时状态。"""
    r = _get_redis()
    if r is None:
        return

    try:
        keys = [
            f"workflow:{workflow_id}:outputs",
            f"workflow:{workflow_id}:completed_segments",
        ]
        r.delete(*keys)
        # state hash 保留 24h 供回查，不主动清理
    except Exception:
        pass
