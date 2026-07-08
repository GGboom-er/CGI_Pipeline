# config/celeryconfig.py
# ── CGI Pipeline v2.0 — 多 DCC 路由配置 ──
import os
from dotenv import load_dotenv
load_dotenv()

broker_url             = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
result_backend         = os.getenv('REDIS_RESULT_URL', 'redis://127.0.0.1:6379/1')

# 启动时重试 broker 连接：扛 redis 尚未就绪/重启竞态（Celery 6.0 起默认关，显式开）
broker_connection_retry_on_startup = True

# 序列化
task_serializer        = 'json'
result_serializer      = 'json'
accept_content         = ['json']

# 重试与 ACK
task_acks_late         = True
task_reject_on_worker_lost = True
result_expires         = 86400

# ── 多 DCC 队列路由 ──
# 默认队列 dcc_queue 由 Maya Worker 消费。
# 启动 Blender/UE Worker 时指定各自队列：
#   celery -A core.tasks worker --pool=solo -Q blender_queue
#   celery -A core.tasks worker --pool=solo -Q ue_queue
task_routes = {
    'core.tasks.execute_dcc_skill': {'queue': 'dcc_queue'},
    'core.tasks.execute_workflow': {'queue': 'workflow_queue'},  # 工作流编排器独立队列
}

# ── 队列声明 ──
# 预声明所有 DCC 队列，确保 Worker 启动时队列已存在
from kombu import Queue
task_queues = (
    Queue('dcc_queue'),       # Maya（默认）
    Queue('blender_queue'),   # Blender
    Queue('ue_queue'),        # Unreal Engine
    Queue('workflow_queue'),  # 工作流编排器（不与 DCC 竞争）
)
task_default_queue = 'dcc_queue'
