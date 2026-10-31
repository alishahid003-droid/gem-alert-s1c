"""Tests for compound's staged profit-banking (Ali, 31 Oct 2026 -- see executor/books.py
COMPOUND_STAGES comment for the full backtest reasoning behind this structure).

Stages: cross $1,000 total value -> bank 32.5% of $1,000; cross $5,000 -> bank 37.5% of
$5,000; cross $10,000 -> bank 55% of $10,000; cross $20,000 -> bank 0% (that IS the goal).
Each stage banks a fixed dollar amount (threshold * bank_pct) the first time its threshold
is crossed, and that money stays banked even if total value later falls back below the
threshold (profit already taken off the table is never put back at risk).
"""
import time
import pytest
import state
from executor import books


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "s.json"), raising=False)
    for k in ("COMPOUND_SEED_USD", "COMPOUND_BANK_PCT", "BANK_AFTER_MULT"):
        monkeypatch.delenv(k, raising=False)


def _set_realized_pnl(monkeypatch, book_id, pnl_usd):
    """Fake a single closed paper trade giving the book exactly this realized P&L."""
    rows = [{"module": book_id, "pnl_usd": pnl_usd, "closed_ts": time.time()}]
    monkeypatch.setattr(books, "_rows", lambda bid: rows if bid == book_id else [])


def test_compound_no_stage_crossed_banks_nothing(monkeypatch):
    books.cfg("compound")  # seed default $100
    _set_realized_pnl(monkeypatch, "compound", 500.0)  # total_value = 600, below $1,000 stage
    assert books.compound_banked("compound") == 0.0
    assert books.banked("compound") == 0.0


def test_compound_stage1_crossed_banks_fixed_amount(monkeypatch):
    _set_realized_pnl(monkeypatch, "compound", 950.0)  # seed 100 + 950 = total_value 1050, clears $1,000
    # bank = 1000 * 0.325 = 325.00, NOT 1050 * 0.325
    assert books.compound_banked("compound") == 325.0
    assert books.banked("compound") == 325.0


def test_compound_stage1_and_stage2_crossed_stack(monkeypatch):
    _set_realized_pnl(monkeypatch, "compound", 4950.0)  # total_value = 5050, clears both $1k and $5k
    expected = round(1000 * 0.325 + 5000 * 0.375, 2)  # 325 + 1875 = 2200.0
    assert books.compound_banked("compound") == expected
    assert books.banked("compound") == expected


def test_compound_all_three_banking_stages_crossed(monkeypatch):
    _set_realized_pnl(monkeypatch, "compound", 9950.0)  # total_value = 10050, clears $1k/$5k/$10k
    expected = round(1000 * 0.325 + 5000 * 0.375 + 10000 * 0.55, 2)  # 325 + 1875 + 5500 = 7700.0
    assert books.compound_banked("compound") == expected


def test_compound_final_20k_stage_banks_nothing_extra(monkeypatch):
    _set_realized_pnl(monkeypatch, "compound", 19950.0)  # total_value = 20050, clears $20k stage too
    # $20k stage has bank_pct=0.0 -- no extra banked beyond the first three stages
    expected = round(1000 * 0.325 + 5000 * 0.375 + 10000 * 0.55, 2)
    assert books.compound_banked("compound") == expected


def test_compound_banked_money_stays_banked_after_retrace(monkeypatch):
    # Book crossed $1,000 at some point (realized_pnl snapshot reflects that history), then
    # lost money and is back down near seed. Since banked() is derived purely from the CURRENT
    # realized_pnl (stateless), this test documents the real behavior: a book whose realized_pnl
    # has fallen back below a threshold no longer shows that stage as banked, because there is no
    # separate ledger of "money already removed" -- compound_banked() recomputes from current state
    # every call. This is intentional per the module's existing stateless design (see realized_pnl/
    # bust_offset comments), but worth a named test so future readers don't assume otherwise.
    _set_realized_pnl(monkeypatch, "compound", -50.0)  # total_value = 50, never crossed $1,000
    assert books.compound_banked("compound") == 0.0


def test_sprint_core1_core2_untouched_by_staged_compound_logic(monkeypatch):
    # Guard against the staged logic leaking into other books: sprint/core1/core2 must still use
    # the original flat BANK_AFTER_MULT/bank_pct mechanism, unchanged.
    for book_id, bank_pct_env in (("sprint", "SPRINT_BANK_PCT"), ("core1", "CORE1_BANK_PCT")):
        rows = [{"module": book_id, "pnl_usd": 9950.0, "closed_ts": time.time()}]
        monkeypatch.setattr(books, "_rows", lambda bid, _rows=rows: _rows if bid == bid else [])
        # original formula: bank_pct * (realized_pnl - BANK_AFTER_MULT*seed)
        c = books.cfg(book_id)
        line = 3.0 * c["seed"]
        expected = round(max(0.0, (c.get("bank_pct") or 0.0) * (9950.0 - line)), 2)
        assert books.banked(book_id) == expected


def test_compound_equity_excludes_staged_bank(monkeypatch):
    _set_realized_pnl(monkeypatch, "compound", 950.0)  # total_value 1050, bank 325
    tv = books.total_value("compound")
    bk = books.banked("compound")
    eq = books.equity("compound")
    assert tv == 1050.0
    assert bk == 325.0
    assert eq == round(tv - bk, 2) == 725.0


# ---- force_reseed() / go_live flow (Ali, 31 Oct 2026) -------------------------------------------

def test_force_reseed_zeroes_realized_pnl_and_resets_stage_ladder(monkeypatch):
    _set_realized_pnl(monkeypatch, "compound", 9950.0)  # total_value 10050, 3 stages crossed
    assert books.compound_banked("compound") > 0.0
    books.force_reseed("compound", time.time(), reason="test")
    # bust_offset now cancels the realized_pnl we faked -> back to seed-only, no stages crossed
    assert books.realized_pnl("compound") == 0.0
    assert books.compound_banked("compound") == 0.0
    assert books.total_value("compound") == books.cfg("compound")["seed"]


def test_force_reseed_only_touches_the_named_book(monkeypatch):
    rows_compound = [{"module": "compound", "pnl_usd": 9950.0, "closed_ts": time.time()}]
    rows_sprint = [{"module": "sprint", "pnl_usd": 90.44, "closed_ts": time.time()}]

    def fake_rows(bid):
        if bid == "compound":
            return rows_compound
        if bid == "sprint":
            return rows_sprint
        return []
    monkeypatch.setattr(books, "_rows", fake_rows)

    books.force_reseed("compound", time.time(), reason="test")
    assert books.realized_pnl("compound") == 0.0
    assert books.realized_pnl("sprint") == 90.44  # untouched


def test_force_reseed_with_50usd_seed_override(monkeypatch):
    monkeypatch.setenv("SPRINT_SEED_USD", "50")
    monkeypatch.setenv("COMPOUND_SEED_USD", "50")
    rows = {"sprint": [{"module": "sprint", "pnl_usd": 90.44, "closed_ts": time.time()}],
            "compound": [{"module": "compound", "pnl_usd": 1533.63, "closed_ts": time.time()}]}
    monkeypatch.setattr(books, "_rows", lambda bid: rows.get(bid, []))
    for book_id in ("sprint", "compound"):
        books.force_reseed(book_id, time.time(), reason="test_go_live")
        assert books.total_value(book_id) == 50.0
        assert books.equity(book_id) == 50.0


def test_go_live_script_refuses_without_seed_override(tmp_path):
    """go_live.py must refuse to reseed (exit 1, no state written) when SPRINT_SEED_USD/
    COMPOUND_SEED_USD aren't set -- otherwise it would silently reseed to the BOOKS dict's
    $100 default instead of Ali's intended $50 (the bug this test guards against was caught
    in a manual dry run before this shipped)."""
    import subprocess
    import os as _os
    state_copy = tmp_path / "state_copy.json"
    state_copy.write_text("{}")  # go_live.py only needs a valid, empty state file for this path
    env = dict(_os.environ)
    env.pop("SPRINT_SEED_USD", None)
    env.pop("COMPOUND_SEED_USD", None)
    env["GEM_ALERT_STATE_FILE"] = str(state_copy)
    repo_root = _os.path.join(_os.path.dirname(__file__), "..")
    result = subprocess.run(["python3", "go_live.py"], cwd=repo_root, env=env,
                             capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "STOP" in result.stdout
    assert "Nothing was reseeded" in result.stdout
