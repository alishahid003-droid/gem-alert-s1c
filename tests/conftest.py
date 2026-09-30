"""Sept 30 2026 -- safety net found while adding tests/test_dashboard_execution_status.py.

Importing dashboard.py (module-level, before `import state`) calls
load_dotenv(), which loads Ali's REAL production .env -- including his
current live UPSTASH_REDIS_REST_URL/TOKEN and TOTAL_WALLET_USD -- into
os.environ for the rest of the pytest PROCESS, not just the one test file
that imported it. Because config.py's CONFIG and executor/config.py's
EXECUTOR_CONFIG are both singletons built from os.environ AT IMPORT TIME,
any test file collected after dashboard.py (pytest collects alphabetically,
so almost everything) can silently inherit:
  - CONFIG.upstash_redis_rest_url/token pointing at the REAL production
    Upstash database instead of an isolated local file (a genuine safety
    hazard for a live trading system's test suite, not just flakiness).
  - EXECUTOR_CONFIG.total_wallet_usd = Ali's real current bankroll (as low
    as single-digit dollars) instead of the test suite's assumed $50
    baseline, shrinking every budget-dependent trigger test's headroom
    until legitimate fires get wrongly rejected as "budget exhausted."

This autouse, function-scoped fixture resets both singletons to their safe
test baseline before every single test, regardless of what any module
import anywhere in the session may have loaded into them. A test that
wants to exercise a different value (real-Upstash pipeline logic, a
specific bankroll tier) sets it back itself via its own monkeypatch,
same as always -- this only fixes what every OTHER test can assume as
its safe starting point.
"""
import pytest

from config import CONFIG
from executor.config import EXECUTOR_CONFIG, bankroll_tier

_SAFE_TOTAL_WALLET_USD = 50.0
_SAFE_TIER = bankroll_tier(_SAFE_TOTAL_WALLET_USD)
_SAFE_STAGE1_POSITION_USD = round(_SAFE_TOTAL_WALLET_USD * _SAFE_TIER["position_pct"], 2)
_SAFE_STAGE2_POSITION_USD = round(_SAFE_STAGE1_POSITION_USD * 0.85, 2)


@pytest.fixture(autouse=True)
def _clean_shared_singletons(monkeypatch):
    monkeypatch.setattr(CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(CONFIG, "upstash_redis_rest_token", None)

    monkeypatch.setattr(EXECUTOR_CONFIG, "total_wallet_usd", _SAFE_TOTAL_WALLET_USD)
    monkeypatch.setattr(EXECUTOR_CONFIG, "bankroll_tier_info", _SAFE_TIER)
    monkeypatch.setattr(EXECUTOR_CONFIG, "stage1_position_usd", _SAFE_STAGE1_POSITION_USD)
    monkeypatch.setattr(EXECUTOR_CONFIG, "stage2_position_usd", _SAFE_STAGE2_POSITION_USD)
    yield
