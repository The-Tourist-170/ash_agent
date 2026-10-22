from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from rq import Queue


def create_redis_connection(url: str) -> Redis:
    return Redis.from_url(url)


def create_async_redis(url: str) -> AsyncRedis:
    return AsyncRedis.from_url(url, decode_responses=True)


def create_queue(name: str, connection: Redis) -> Queue:
    return Queue(name, connection=connection)
