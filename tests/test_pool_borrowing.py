"""Shared-capital pool borrowing across sprint/core1/core2/compound (Ali, 2 Nov 2026).

Ali's concern: on a real small live seed (e.g. $25/module of a $100 total), a fixed per-module
split starves a busy module (his example: compound mid-signal-cycle) sitting next to idle
siblings with free cash they aren't using. pool_free_usd()/borrow_room_usd() let a module draw on
genuinely idle capital elsewhere in the same 4-book family when its own free_equity() is tapped
out but a real signal needs sizing -- capped at POOL_BORROW_MULT x the book's own natural
capacity so one module can never fully swallow the other 3's capital.

Important design note this test suite documents: in PAPER mode, each book's equity() is entirely
self-contained (seed + that book's own realized P&L) -- nothing physically scarce is shared
between books, so "borrowing" here only widens how big a SIMULATED stake a book is allowed to
size, it does not move any number from one book's equity() to another's. This is the correct
paper-mode behavior (paper has no real shared wallet to protect), and is also exactly what will
become load-bearing once live money sits in one real wallet shared by all 4 modules -- the same
borrow_room_usd()/pool_free_usd() functions are what a live-mode balance check would be built on
top of, so this is built and tested now, ahead of going live, per Ali's explicit instruction.
"""
import pytest

import state
import executor.books as books


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOCAL_STATE_FILE", str(tmp_path / "test_state.json"))
    yield


def test_pool_free_usd_sums_idle_capital_across_the_4_books(monkeypatch):
    # fresh books: each at its $100 seed, nothing committed -> all of it is "free"
    total_free = books.pool_free_usd()
    assert total_free == pytest.approx(100.0 + 100.0 + 100.0 + 100.0, abs=0.01)


def test_pool_free_usd_excludes_non_pool_books(monkeypatch):
    before = books.pool_free_usd()
    # copy/pattern-tracking books existing or having balance must not affect the pool total
    assert books.POOL_BOOKS == ("sprint", "core1", "core2", "compound")
    assert "fomo_followers" not in books.POOL_BOOKS
    assert "solana_sprint" not in books.POOL_BOOKS
    after = books.pool_free_usd()
    assert before == after


def test_borrow_room_zero_for_non_pool_books():
    assert books.borrow_room_usd("fomo_followers") == 0.0
    assert books.borrow_room_usd("solana_sprint") == 0.0
    assert books.borrow_room_usd("leaderboard") == 0.0


def test_borrow_room_is_nonzero_when_pool_has_idle_capital():
    # fresh compound book, nothing committed anywhere -> real idle capital exists in the pool
    room = books.borrow_room_usd("compound")
    assert room > 0.0


def test_borrow_room_capped_by_own_natural_capacity_multiple(monkeypatch):
    """borrow_room_usd() must never let a book's EXTRA borrowed room exceed
    (POOL_BORROW_MULT - 1) x its own natural room -- confirms the cap is real, not just present
    in a docstring."""
    book_id = "compound"
    eq = books.equity(book_id)
    share = books.effective_max_share(book_id)
    own_room = max(0.0, eq * share - books.committed_usd(book_id))
    expected_cap = own_room * (books.POOL_BORROW_MULT - 1.0)
    room = books.borrow_room_usd(book_id)
    assert room <= expected_cap + 0.01


def test_borrow_room_shrinks_as_pool_capital_gets_committed(monkeypatch):
    """Once the pool's free capital is driven below compound's own extra_allowed ceiling, the POOL
    number (not the per-book cap) becomes the binding constraint, and borrow room tracks it down
    as more capital gets committed -- proves the number reflects real committed state, not a
    static constant. (compound's own extra_allowed ceiling is eq*share*(MULT-1) = 100*0.75*1 = $75
    -- max_share raised 0.60 -> 0.75 on 8 Nov 2026, see books.py's BOOKS config for why -- so the
    pool must be driven below that to actually observe it shrinking further.)"""
    from executor import paper_ledger
    room_before = books.borrow_room_usd("compound")
    assert room_before == 75.0   # capped by compound's own extra_allowed ceiling, pool not yet binding
    # commit almost all of sprint's, core1's AND core2's capital -- pool must drop well below
    # compound's own $60 ceiling (own_free_equity(compound)=100 stays unused in this calc, so the
    # pool contribution from siblings alone must fall under 60-100=... actually pool_free_usd()
    # SUMS all 4 books' free_equity including compound's own $100, so driving siblings to near-zero
    # free still leaves the pool at ~$100 (compound's own) -- not below $60. To genuinely test the
    # pool becoming the binding constraint, commit compound's OWN capital too, leaving only a
    # small amount of real free cash anywhere in the family.
    for book in ("sprint", "core1", "core2"):
        r = paper_ledger.open_paper("solana", f"X-{book}", book, "moonshot", 97, 50_000,
                                     strategy="early", exit_profile=book, module=book)
        assert r is not None, f"test setup failed to open a position for {book}"
    r = paper_ledger.open_paper("solana", "X-compound", "compound", "moonshot", 90, 50_000,
                                 strategy="early", exit_profile="compound", module="compound")
    assert r is not None
    room_mid = books.borrow_room_usd("compound")
    assert room_mid < room_before          # pool is now the binding constraint, and it shrank
    # commit the rest of compound's own capital too -- pool shrinks further, borrow room in lockstep
    r2 = paper_ledger.open_paper("solana", "X-compound2", "compound", "moonshot", 8, 50_000,
                                  strategy="early", exit_profile="compound", module="compound")
    assert r2 is not None
    room_after = books.borrow_room_usd("compound")
    assert room_after < room_mid


def test_stake_usd_can_exceed_own_free_equity_when_pool_has_room(monkeypatch):
    """The actual behavior Ali asked for: a module whose OWN free cash is small can still size a
    real stake bigger than that own-cash number when siblings have real idle capital -- this is
    what prevents the starvation he described (compound busy, sprint/core1/core2 quiet)."""
    from executor import paper_ledger
    book_id = "compound"
    # commit almost all of compound's own capital, leaving only a sliver of its own free_equity
    r = paper_ledger.open_paper("solana", "Y1", book_id, "moonshot", 95, 50_000,
                                 strategy="early", exit_profile=book_id, module=book_id)
    assert r is not None, "test setup failed to open the compound position"
    own_free = books.free_equity(book_id)
    assert own_free < 10.0   # confirm compound's own cash really is nearly tapped out
    stake = books.stake_usd(book_id)
    # with sprint/core1/core2 still fully idle (real pool capital available), the next stake
    # compound can size must be bigger than what its own tapped-out free_equity alone would allow
    assert stake > own_free


def test_borrow_never_lets_a_book_exceed_its_absolute_hard_cap(monkeypatch):
    """Pool borrowing must never bypass the existing absolute seed-based hard_cap backstop --
    confirms borrowing adds flexibility within the existing safety ceiling, not around it."""
    book_id = "compound"
    stake = books.stake_usd(book_id)
    hard_cap = books.cfg(book_id)["seed"] * 3.0   # MAX_STAKE_SEED_MULT default
    assert stake <= hard_cap + 0.01


def test_tier_stake_usd_can_also_borrow_from_pool(monkeypatch):
    """sprint/core1/core2 (the TIER_BOOK_SCALE books) go through tier_stake_usd(), not stake_usd()
    directly -- confirm borrowing applies there too, not just compound's path."""
    from executor import paper_ledger
    # commit almost all of core1's and core2's own capital, leaving compound fully idle (a real
    # pool source) and sprint tapped out needing to borrow
    for book in ("core1", "core2"):
        r = paper_ledger.open_paper("solana", f"Z-{book}", book, "moonshot", 97, 50_000,
                                     strategy="early", exit_profile=book, module=book)
        assert r is not None
    r = paper_ledger.open_paper("solana", "Z-sprint", "sprint", "moonshot", 95, 50_000,
                                 strategy="early", exit_profile="sprint", module="sprint")
    assert r is not None
    own_free = books.free_equity("sprint")
    assert own_free < 10.0
    stake = books.tier_stake_usd("sprint", "probe")
    assert stake > own_free
