import asyncio
import pytest
from ferrox_py_aspyre.security.crypto_vault import CryptoVault
from ferrox_py_aspyre.security.oauth_coordinator import (
    ExactOAuthTokenCoordinator,
    MockExactOAuthClient,
)


@pytest.mark.asyncio
async def test_chaos_token_refresh_zero_race_under_high_concurrency(fake_redis):
    """
    CHAOS TEST: 20 concurrent workers all discover an expired token simultaneously.
    If more than one worker attempts to refresh the single-use token, MockExactOAuthClient
    raises a fatal exception simulating Exact Online burning the refresh token.
    """
    oauth_client = MockExactOAuthClient()
    vault = CryptoVault()
    coordinator = ExactOAuthTokenCoordinator(fake_redis, crypto_vault=vault, oauth_client=oauth_client)

    # Initialize with expired token
    division_id = "DIV-NL-CHAOS-001"
    await coordinator.initialize_tokens(
        division_id=division_id,
        access_token="EXPIRED-ACCESS-001",
        refresh_token="SINGLE-USE-REFRESH-001",
        expires_in_seconds=-10,  # Expired 10 seconds ago
    )

    # Launch 20 concurrent tasks attempting to fetch a valid access token
    async def worker_fetch(worker_id: int) -> str:
        return await coordinator.get_valid_access_token(division_id)

    tasks = [worker_fetch(i) for i in range(20)]
    results = await asyncio.gather(*tasks)

    # 1. All 20 workers must receive a non-empty, valid access token
    assert len(results) == 20
    assert all(r.startswith("EXACT-ACCESS-") for r in results)

    # 2. All workers must have received the EXACT same refreshed token
    unique_tokens = set(results)
    assert len(unique_tokens) == 1

    # 3. CRITICAL: The single-use refresh endpoint must have been called EXACTLY ONCE!
    assert oauth_client.refresh_call_count == 1
    assert len(oauth_client.used_refresh_tokens) == 1
