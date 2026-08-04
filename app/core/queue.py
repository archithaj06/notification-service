import redis
from rq import Queue

from app.config import settings
from app.models.base import Priority

redis_conn = redis.Redis.from_url(settings.redis_url)

# One named queue per priority level. An `rq worker` process started as:
#   rq worker --with-scheduler critical high normal low
# pulls jobs from `critical` first, and only checks `high` once `critical`
# is empty, and so on -- this is how RQ workers process multiple queues,
# and it directly satisfies "critical/high priority messages should be
# processed before normal/low priority ones."
QUEUE_NAMES_IN_PRIORITY_ORDER = [
    Priority.CRITICAL.value,
    Priority.HIGH.value,
    Priority.NORMAL.value,
    Priority.LOW.value,
]

queues: dict[str, Queue] = {
    name: Queue(name, connection=redis_conn) for name in QUEUE_NAMES_IN_PRIORITY_ORDER
}


def get_queue_for_priority(priority: Priority) -> Queue:
    return queues[priority.value]
