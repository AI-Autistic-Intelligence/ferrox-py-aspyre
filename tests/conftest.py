import pytest
import pytest_asyncio
import fakeredis.aioredis


@pytest_asyncio.fixture
async def fake_redis():
    """
    In-memory Redis client with Lua scripting support via fakeredis[lua].
    """
    server = fakeredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=False)
    yield client
    await client.flushall()
    await client.aclose()
