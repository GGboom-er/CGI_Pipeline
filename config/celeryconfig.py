# config/celeryconfig.py
# ── CGI Pipeline v2.0 — 单队列配置 ──
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

# ── 单 CGI 队列路由 ──
# DCC 类型只决定适配器，不再决定 Celery 队列。所有任务由一个串行 Worker
# 消费，workflow 在该 Worker 内同步执行，避免嵌套任务和跨队列等待。
task_routes = {
    'core.tasks.execute_api_operation': {'queue': 'cgi_queue'},
    'core.tasks.execute_api_chain': {'queue': 'cgi_queue'},
    'core.tasks.execute_workflow': {'queue': 'cgi_queue'},
}

# ── 队列声明 ──
from kombu import Queue
task_queues = (
    Queue('cgi_queue'),
)
task_default_queue = 'cgi_queue'
