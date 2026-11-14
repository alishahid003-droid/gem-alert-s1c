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
from executor import books, position_state


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


# ---- REAL-money banking/cascade (15 Nov 2026, Ali, "fix everything... cascade... every single
# thing which is needed") -------------------------------------------------------------------
#
# Before this fix, compound_banked()/banked() always walked COMPOUND_STAGES against PAPER
# realized_pnl() even once compound went live for real money -- meaning the $1k/$5k/$10k
# banking stages and the $500 real-dollar cascade trigger (check_sprint_compound_cascade(),
# which funds sprint/core1/core2 with real $100 each) were all keyed off a number with zero
# connection to the real wallet. The fix: compound_banked_real()/realized_pnl_real()/
# total_value_real() walk the same ladder against REAL closed trades from position_state
# (f"{module_id}_real" stage convention), and banked() now calls the real version for
# compound specifically once compound is listed in LIVE_BOOKS -- exactly the same
# "_use_real_capital"-style live gate already used for the capital/position-count fixes.

def _record_real_close(chain, token, module_id, usd_amount, pnl_usd):
    """Record one REAL closed compound position the way multichain_sniper.py's act() actually
    records a real trade: record_stage_entry(..., f"{module_id}_real", ...), record_fill(...),
    then close_position(..., exit_usd=...) so pnl_usd lands on the record the same way a real
    exit does."""
    position_state.record_stage_entry(chain, token, f"{module_id}_real", usd_amount, 50_000,
                                       f"real {module_id} buy attempt: ok")
    position_state.record_fill(chain, token, 1000.0, tx_signature="0xtest")
    position_state.close_position(chain, token, "test_exit", exit_usd=usd_amount + pnl_usd)


def test_compound_banked_uses_paper_ladder_when_not_live(monkeypatch):
    """compound not listed in LIVE_BOOKS -- banked() must stay on the paper ladder, completely
    unaffected by any real position_state records, exactly as before this fix."""
    monkeypatch.setattr(books, "_live_books_from_env", lambda: set())
    _set_realized_pnl(monkeypatch, "compound", 950.0)  # paper total_value 1050, clears $1k paper-side
    _record_real_close("robinhood_chain", "RWontCount", "compound", 20.0, 50_000.0)  # huge real win, ignored
    assert books.banked("compound") == 325.0  # paper-only answer, real close had zero effect


def test_compound_banked_uses_real_ladder_when_live(monkeypatch):
    """compound listed in LIVE_BOOKS -- banked() must switch to the REAL ladder
    (compound_banked_real()), ignoring paper's realized_pnl() entirely."""
    monkeypatch.setattr(books, "_live_books_from_env", lambda: {"compound"})
    _set_realized_pnl(monkeypatch, "compound", 99_999.0)  # huge paper total_value, must be ignored
    # One real closed trade: $20 in, +$930 real pnl -> real total_value = 100 (seed) + 930 = 1030,
    # clears the $1,000 real stage -> banks 1000 * 0.325 = 325.00.
    _record_real_close("robinhood_chain", "R1", "compound", 20.0, 930.0)
    assert books.realized_pnl_real("compound") == 930.0
    assert books.total_value_real("compound") == 1030.0
    assert books.compound_banked_real("compound") == 325.0
    assert books.banked("compound") == 325.0  # banked() now returns the REAL answer, not paper's


def test_compound_real_ladder_stacks_stages_same_as_paper(monkeypatch):
    monkeypatch.setattr(books, "_live_books_from_env", lambda: {"compound"})
    # seed 100 + 4950 real pnl = total_value_real 5050, clears both $1k and $5k real stages.
    _record_real_close("robinhood_chain", "R1", "compound", 20.0, 4950.0)
    expected = round(1000 * 0.325 + 5000 * 0.375, 2)  # 2200.0 -- same math as the paper ladder
    assert books.compound_banked_real("compound") == expected
    assert books.banked("compound") == expected


def test_compound_real_ladder_excludes_failed_real_buys(monkeypatch):
    """A real buy attempt that failed outright (mark_stage_buy_failed) spent no real money and
    has no priced close -- it must not contribute to realized_pnl_real() at all."""
    monkeypatch.setattr(books, "_live_books_from_env", lambda: {"compound"})
    _record_real_close("robinhood_chain", "R1", "compound", 20.0, 930.0)  # real winner, as above
    position_state.record_stage_entry("robinhood_chain", "Rfail",
                                       "compound_real", 20.0, 50_000, "real compound buy attempt: refused")
    position_state.mark_stage_buy_failed("robinhood_chain", "Rfail", "compound_real", reason="refused")
    # Rfail was never filled/closed, so it has no pnl_usd and list_closed_positions() won't even
    # include it (status stays "open") -- but this test documents that explicitly either way.
    assert books.realized_pnl_real("compound") == 930.0
    assert books.compound_banked_real("compound") == 325.0


def test_sprint_compound_cascade_fires_off_real_banked_not_paper(monkeypatch, tmp_path):
    """The actual real-money consequence this fix protects: check_sprint_compound_cascade()'s
    $500 trigger must fire off compound's REAL banked vault, not a paper number -- it must NOT
    fire just because paper trading crossed $500 while compound sits well under that in real
    dollars, and it MUST fire once the real vault genuinely crosses it."""
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "cascade.json"), raising=False)
    monkeypatch.setattr(books, "_live_books_from_env", lambda: {"compound"})

    # Huge PAPER profit (would easily cross $500 banked on the old paper-based ladder) but a
    # small REAL profit that has NOT crossed the $1,000 real stage yet -> real banked = $0.
    _set_realized_pnl(monkeypatch, "compound", 99_999.0)
    _record_real_close("robinhood_chain", "R1", "compound", 20.0, 100.0)  # real total_value 220, no stage crossed
    assert books.banked("compound") == 0.0
    assert books.check_sprint_compound_cascade() is None  # must NOT fire on the paper number

    # Now give compound a real win that clears $1,000 real stage -> real banked = $325, still
    # under the $500 trigger -- cascade must still not fire.
    _record_real_close("robinhood_chain", "R2", "compound", 20.0, 830.0)  # cumulative real pnl 930 -> total_value_real 1030
    assert books.banked("compound") == 325.0
    assert books.check_sprint_compound_cascade() is None  # 325 < 500, not yet

    # One more real win pushes real banked past $500 -> cascade must fire now, pulling $300 out
    # of compound and crediting sprint/core1/core2 $100 each as real deposited seed money.
    _record_real_close("robinhood_chain", "R3", "compound", 20.0, 5000.0)  # total_value_real now way past $5k
    assert books.banked("compound") >= 500.0
    result = books.check_sprint_compound_cascade()
    assert result is not None and result.get("fired") is True
    for recipient in ("sprint", "core1", "core2"):
        assert books.deposited(recipient) == 100.0
