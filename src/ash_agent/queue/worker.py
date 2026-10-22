import asyncio
import logging

from rq import Queue, Worker

from ash_agent.config import get_settings
from ash_agent.observability.logging import configure_logging
from ash_agent.persistence.checkpoints import setup_checkpointer
from ash_agent.queue.connection import create_redis_connection

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    asyncio.run(setup_checkpointer(settings))
    logger.info("checkpoint schema ready")

    connection = create_redis_connection(settings.valkey_url)
    queue = Queue(settings.rq_queue_name, connection=connection)
    worker = Worker([queue], connection=connection)

    logger.info("worker listening on queue %s", settings.rq_queue_name)
    worker.work(with_scheduler=False)
