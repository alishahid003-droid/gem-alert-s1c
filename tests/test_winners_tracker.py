import json
import winners_tracker as wt


def pool(addr="A1", mcap=20_000.0, created=1_000.0, chain="solana"):
    return {"chain": chain, "addr": addr, "name": "COIN", "mcap": mcap, "created": created, "dex": "x"}


def test_record_peak_entered_and_winner(tmp_path, monkeypatch):
    b = wt.Book(str(tmp_path / "b.json"))
    b.record(pool(), now=1_300.0)
    assert b.coins["solana:A1"]["first_age_min"] == 5.0
    b.mark_entered("solana", "A1")
    calls = []
    def fake(slug, addrs):
        calls.append((slug, addrs))
        return {"A1": 3_500_000.0}
    assert b.due(now=1_300.0 + 3600) == []
    assert b.revisit(fake, {"solana": "solana"}, now=1_300.0 + 3 * 3600 + 5) == 1
    c = b.coins["solana:A1"]
    assert c["peak_mcap"] == 3_500_000.0 and c["done"] == [3] and c["entered"]
    assert [w["addr"] for w in b.winners()] == ["A1"]
    assert b.revisit(fake, {"solana": "solana"}, now=1_300.0 + 3 * 3600 + 10) == 0     # 3h check done; next is 24h
    b.save()
    assert wt.Book(str(tmp_path / "b.json")).coins["solana:A1"]["peak_mcap"] == 3_500_000.0


def test_prune_keeps_winners_drops_stale_losers(tmp_path):
    b = wt.Book(str(tmp_path / "b.json"))
    b.record(pool("L", 5_000.0), now=1_000.0)
    b.record(pool("W", 5_000.0), now=1_000.0)
    b.coins["solana:W"]["peak_mcap"] = 2_000_000.0
    b.prune(now=1_000.0 + 20 * 86400)
    assert list(b.coins) == ["solana:W"]


def test_pumpfun_runners_from_early_log(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "early_log").mkdir()
    rows = [{"t": "create", "mint": "M", "sym": "RUN", "mcap_sol": 30, "init_sol": 1, "creator_launches": 2, "creator_grads": 1},
            {"t": "outcome", "mint": "M", "listed": True, "mcap_usd": 400_000.0, "cp_min": 15},
            {"t": "outcome", "mint": "X", "listed": True, "mcap_usd": 10_000.0, "cp_min": 15}]
    (tmp_path / "early_log" / "2026-10-13.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    out = wt.pumpfun_runners()
    assert len(out) == 1 and out[0]["sym"] == "RUN" and out[0]["peak_seen"] == 400_000.0
