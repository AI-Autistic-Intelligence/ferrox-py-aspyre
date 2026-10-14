import asyncio
import time
import pytest
from ferrox_py_aspyre.twinfield.session_pooler import (
    TwinfieldSessionPooler,
    MockTwinfieldAuthenticator,
    TwinfieldSession,
)


@pytest.mark.asyncio
async def test_session_pooler_caching_and_touch(fake_redis):
    auth = MockTwinfieldAuthenticator(default_cluster="https://c3.twinfield.com")
    pooler = TwinfieldSessionPooler(fake_redis, authenticator=auth)

    # First call: performs login
    session1 = await pooler.get_valid_session("OFFICE01", "USER01", "password123")
    assert session1.office_code == "OFFICE01"
    assert session1.cluster_url == "https://c3.twinfield.com"
    assert auth.call_count == 1

    # Second call: returns cached session without invoking logon
    session2 = await pooler.get_valid_session("OFFICE01", "USER01", "password123")
    assert session2.session_id == session1.session_id
    assert auth.call_count == 1


@pytest.mark.asyncio
async def test_session_pooler_singleflight_concurrent_logon(fake_redis):
    auth = MockTwinfieldAuthenticator()
    pooler = TwinfieldSessionPooler(fake_redis, authenticator=auth)

    # 10 concurrent requests for the same office/user
    sessions = await asyncio.gather(
        *(pooler.get_valid_session("OFFICE01", "USER01", "pass") for _ in range(10))
    )

    # All workers must receive the exact same session ID
    session_ids = {s.session_id for s in sessions}
    assert len(session_ids) == 1
    # Only 1 actual logon call should have been made
    assert auth.call_count == 1


@pytest.mark.asyncio
async def test_session_invalidation_and_keepalive(fake_redis):
    auth = MockTwinfieldAuthenticator()
    pooler = TwinfieldSessionPooler(fake_redis, authenticator=auth)

    session = await pooler.get_valid_session("OFFICE01", "USER01", "pass")
    
    # Run keep alive
    success = await pooler.run_keep_alive(session)
    assert success is True

    # Invalidate session
    await pooler.invalidate_session("OFFICE01", "USER01")

    # Next call must re-authenticate
    new_session = await pooler.get_valid_session("OFFICE01", "USER01", "pass")
    assert new_session.session_id != session.session_id
    assert auth.call_count == 2
