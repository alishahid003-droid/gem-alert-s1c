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
def _clean_state_cache():
    """Oct 5 2026: state.py now keeps a short-TTL read cache and an Upstash
    quota-block flag at module level; neither may leak between tests."""
    import state as _state
    _state._READ_CACHE.clear()
    _state._UPSTASH_BLOCK.update({"until": 0.0, "reason": "", "last_print": 0.0})
    yield
    _state._READ_CACHE.clear()
    _state._UPSTASH_BLOCK.update({"until": 0.0, "reason": "", "last_print": 0.0})


@pytest.fixture(autouse=True)
def _clean_shared_singletons(monkeypatch):
    monkeypatch.setattr(CONFIG, "upstash_redis_rest_url", None)
    monkeypatch.setattr(CONFIG, "upstash_redis_rest_token", None)

    monkeypatch.setattr(EXECUTOR_CONFIG, "total_wallet_usd", _SAFE_TOTAL_WALLET_USD)
    monkeypatch.setattr(EXECUTOR_CONFIG, "bankroll_tier_info", _SAFE_TIER)
    monkeypatch.setattr(EXECUTOR_CONFIG, "stage1_position_usd", _SAFE_STAGE1_POSITION_USD)
    monkeypatch.setattr(EXECUTOR_CONFIG, "stage2_position_usd", _SAFE_STAGE2_POSITION_USD)
    yield


# 1 Nov 2026 (Ali): found live -- adding SPRINT_SEED_USD=50 / COMPOUND_SEED_USD=50 to the real
# .env (for the go_live.py $50 reseed flow) leaked through the SAME dashboard.py load_dotenv()
# path the docstring above already warns about, except into executor/books.py's cfg() function,
# which reads os.environ directly on every call rather than through a singleton -- so the existing
# _clean_shared_singletons fixture above (which only resets CONFIG/EXECUTOR_CONFIG) didn't catch
# it. 7 tests across test_router_books.py and test_sprint.py failed on a real run, all expecting
# the old $100 default seed and getting $50 instead -- not a code regression, a test-isolation gap
# that only surfaced once .env's actual seed values diverged from the suite's hardcoded $100
# assumption. Strips every *_SEED_USD / *_TARGET_USD / *_DAYS book-config override pytest's own
# process may have inherited from a real .env, before each test, same autouse pattern as above.
_BOOK_ENV_VARS_TO_CLEAR = (
    "SPRINT_SEED_USD", "SPRINT_TARGET_USD", "SPRINT_DAYS",
    "COMPOUND_SEED_USD", "COMPOUND_BANK_PCT",
    "CORE1_SEED_USD", "CORE1_TARGET_USD", "CORE1_DAYS",
    "CORE2_SEED_USD", "CORE2_TARGET_USD", "CORE2_DAYS",
    # ADDED 14 Nov 2026 (Claude, found while fixing stale multichain_sniper tests): same leak
    # class as the vars above -- Ali's real .env sets LIVE_BOOKS=compound (the current real go-live
    # state, compound-only until it banks $500 and the Nov 13 cascade seeds sprint/core1/core2),
    # and executor/books.py's _live_books_from_env() reads os.environ directly on every call, not
    # through a singleton this fixture file already resets. Left uncleared, every test in this
    # file that evaluates a pool through core1/core2/sprint/compound silently inherited the REAL
    # live-armed state and got refused with "paused -- compound is live and no trades ... until
    # the cross-module cascade seeds this book" -- not a code bug, a test-isolation gap, same
    # shape as the *_SEED_USD leak documented above it.
    "LIVE_BOOKS",
)


@pytest.fixture(autouse=True)
def _clean_book_env_overrides(monkeypatch):
    for k in _BOOK_ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(k, raising=False)
    yield


# 1 Nov 2026 (Ali audit): FOMOAPI_DAILY_CREDIT_BUDGET=0 is now set in the real .env (Fomo
# intentionally paused while credits are repaced over a month -- see layer13_fomo_copytrade.py's
# _daily_budget()). That function reads os.environ DIRECTLY on every call, same leak class as the
# *_SEED_USD vars above, and nothing reset it -- so every test that exercises _fomo_get()'s success
# path (fetch_trader_pnl, fetch_trader_profile, fetch_fomo_alerts, etc.) got silently refused with
# "daily credit budget reached (0/0)" no matter what the test's own get_json mock returned. Looked
# like file-order-dependent flakiness (passed when a file ran alone and had already monkeypatched
# its own temporary budget via setenv; failed once collected with other files that hadn't) but the
# real cause was this single inherited env var, present for the whole pytest process regardless of
# order. Giving tests a real, non-zero default budget here removes the false "flake" entirely --
# any test that specifically wants to exercise the 0-budget/exhausted path still sets it itself via
# monkeypatch.setenv, same as test_layer13_fomo_copytrade.py already does for its 402 test.
_FOMO_ENV_VARS_TO_CLEAR = ("FOMOAPI_DAILY_CREDIT_BUDGET", "FOMO_ALERTS_MIN_INTERVAL_SECONDS",
                           "FOMO_LEADERBOARD_INTERVAL_SECONDS")


@pytest.fixture(autouse=True)
def _clean_fomo_env_overrides(monkeypatch):
    for k in _FOMO_ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture(autouse=True)
def _paper_stats_include_all(monkeypatch):
    """Fixture timestamps predate the live paper-stats cutoff
    (paper_ledger.STATS_SINCE_TS); count every test trade."""
    import executor.paper_ledger as _pl
    monkeypatch.setattr(_pl, "STATS_SINCE_TS", 0.0)
    yield


import pytest as _pytest


@_pytest.fixture(autouse=True)
def _legacy_paper_on_for_tests(monkeypatch):
    """Production keeps the legacy stage/scalper paper traders OFF (LEGACY_PAPER_ENABLED); the ledger tests exercise them."""
    monkeypatch.setenv("LEGACY_PAPER_ENABLED", "true")
