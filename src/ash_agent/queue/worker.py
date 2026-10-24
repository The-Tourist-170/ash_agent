import asyncio
import logging
import sys

from rq import Queue, SimpleWorker, Worker

from ash_agent.config import get_settings
from ash_agent.observability.logging import configure_logging
from ash_agent.persistence.checkpoints import setup_checkpointer
from ash_agent.queue.connection import create_redis_connection

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_colors)

    asyncio.run(setup_checkpointer(settings))
    logger.info("checkpoint schema ready")

    connection = create_redis_connection(settings.valkey_url)
    queue = Queue(settings.rq_queue_name, connection=connection)
    worker_class = SimpleWorker if sys.platform == "darwin" else Worker
    worker = worker_class([queue], connection=connection)

    logger.info("worker (%s) listening on queue %s", worker_class.__name__, settings.rq_queue_name)
    worker.work(with_scheduler=True)
