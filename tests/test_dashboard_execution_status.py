"""Sept 30 2026 (Ali: "i have not placed my keys neither have i placed
funds in the wallet then how come those 2 came") -- dashboard.py's
_execution_status() must correctly distinguish a real, filled buy from a
position that was only ever recorded as a trade DECISION (staked $ locked
in) with the actual swap failing or never resolving. Before this, the
dashboard showed identical "$X staked" rows for both cases."""
import dashboard


def test_confirmed_fill_reports_real():
    pos = {"fill_status": "confirmed", "amount_tokens": 1234.5, "stages": {"stage1": {}}}
    status, detail = dashboard._execution_status(pos)
    assert status == "real"
    assert "1234" in detail or "1,234" in detail


def test_failed_stage_reports_failed_even_with_staked_usd():
    pos = {
        "total_usd": 15.0,
        "stages": {"stage1": {"buy_status": "failed", "fail_reason": "no execution wallet key configured for chain 'bsc'"}},
    }
    status, detail = dashboard._execution_status(pos)
    assert status == "failed"
    assert "NO real money moved" in detail
    assert "no execution wallet key configured" in detail


def test_unconfirmed_amount_reports_unconfirmed():
    pos = {"fill_status": "unconfirmed_amount", "stages": {"stage1": {}}}
    status, detail = dashboard._execution_status(pos)
    assert status == "unconfirmed"


def test_no_resolution_yet_reports_pending():
    pos = {"stages": {"stage1": {}}}
    status, detail = dashboard._execution_status(pos)
    assert status == "pending"


def test_confirmed_fill_takes_priority_over_a_different_stage_failing():
    # e.g. stage1 filled for real, stage2 later failed -- must still show
    # the real fill, not get swallowed by the other stage's failure.
    pos = {
        "fill_status": "confirmed", "amount_tokens": 500.0,
        "stages": {"stage1": {}, "stage2": {"buy_status": "failed", "fail_reason": "budget exhausted"}},
    }
    status, detail = dashboard._execution_status(pos)
    assert status == "real"
