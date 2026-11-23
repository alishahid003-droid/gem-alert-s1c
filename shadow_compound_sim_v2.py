"""Shadow compound simulator v2 (20 Nov 2026, Ali's real correction): fills the real gap in
winners_tracker.py's +3h/+24h/+72h coarse revisit cadence by running a CONTINUOUS,
short-interval simulated walk of compound's real exit ladder against real live price data,
for every real candidate that qualifies for compound's lane on robinhood_chain/base.

20 Nov 2026 (Ali, real correction -- this is the v2 rewrite): the FIRST version of this
script gave every single qualifying candidate its own free, uncapped synthetic $100 --
no shared equity pool, no capacity cap, no connection at all to the $115 Ali actually
plans to fund the real system with. That made the results (it opened 23 "positions" at
once, one closed at +$481 on a "$600" stake) meaningless as a preview of what will
actually happen at $115 real equity -- a real design mistake, not a bug, and Ali is right
to have caught it ("why did you even plan that? did you ask me?"). This version fixes it:
a single SHARED synthetic equity pool starting at SHADOW_START_EQUITY_USD (default 115.0,
Ali's actual funding plan), with per-trade stake sized the same way compound's real
executor/books.py sizes it (shot_pct of equity, capped at max_share of equity minus
already-committed, same constants: shot_pct=0.50, max_share=0.75 -- see books.py's
BOOKS["compound"] and stake_usd()/max_open()), so new candidates can only open when there
is actually synthetic capital free, and equity compounds/draws down exactly like the real
book would. A candidate is skipped (not opened) when the pool can't fund even the floor
stake -- this mirrors can_open()'s real "book is empty" refusal instead of hiding it.

SAFETY (Ali's explicit, standing constraint -- "the paper trade or anything should not
overlap with the real trading and creating a mess and a havoc for us"):
  - This script NEVER imports or calls executor/books.py's real stake/equity/can_open
    functions (deliberately -- those are wired into state.py's shared state store, real
    cascade/phase-target gates, and the real "compound" book's live flags; calling them
    here risks touching real shared state). Instead it re-implements the SAME sizing
    *formula* (shot_pct/max_share/floor) as a fully self-contained, in-memory float --
    _ShadowEquity below -- with zero calls into state.py or books.py.
  - NEVER touches state.py's shared state store, NEVER calls executor/paper_ledger.py's
    open_position()/manage() (which write to the real paper ledger sqlite/journal that
    feeds real equity() numbers compound and the cascade read).
  - Builds its OWN, fully synthetic, in-memory `pos` dict matching the shape executor/
    paper_ledger.py's _manage_early() expects, and calls ONLY that one pure function.
  - Writes ONLY to its own new file, logs/shadow_compound_sim.jsonl.
  - Read-only against candidate_eval.jsonl.

Usage: python shadow_compound_sim_v2.py
Env overrides: SHADOW_START_EQUITY_USD (default 115.0), SHADOW_POLL_INTERVAL_S (default 20),
SHADOW_MAX_HOLD_MIN (default 180), SHADOW_SHOT_PCT (default 0.50), SHADOW_MAX_SHARE (default 0.75).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SHADOW_LOG = os.path.join("logs", "shadow_compound_sim.jsonl")
CANDIDATE_LOG = os.path.join("logs", "candidate_eval.jsonl")
POLL_INTERVAL_S = float(os.environ.get("SHADOW_POLL_INTERVAL_S", "20"))
MAX_HOLD_MIN = float(os.environ.get("SHADOW_MAX_HOLD_MIN", "180"))

START_EQUITY_USD = float(os.environ.get("SHADOW_START_EQUITY_USD", "115.0"))
SHOT_PCT = float(os.environ.get("SHADOW_SHOT_PCT", "0.50"))
MAX_SHARE = float(os.environ.get("SHADOW_MAX_SHARE", "0.75"))
STAKE_FLOOR_USD = float(os.environ.get("SHADOW_STAKE_FLOOR_USD", "15.0"))
MIN_EQUITY_TO_TRADE = float(os.environ.get("SHADOW_MIN_EQUITY_USD", "5.0"))


class _ShadowEquity:
    def __init__(self, start_usd):
        self.realized_pnl = 0.0
        self.start_usd = start_usd
        self.committed_usd = 0.0

    @property
    def equity(self):
        return round(max(0.0, self.start_usd + self.realized_pnl), 2)

    def next_stake(self):
        eq = self.equity
        if eq < MIN_EQUITY_TO_TRADE:
            return 0.0
        base = eq * SHOT_PCT
        room_left = max(0.0, eq * MAX_SHARE - self.committed_usd)
        stake = min(base, room_left)
        if stake < STAKE_FLOOR_USD:
            return 0.0
        return round(stake, 2)

    def open_stake(self, usd):
        self.committed_usd = round(self.committed_usd + usd, 2)

    def close_stake(self, usd, pnl_usd):
        self.committed_usd = round(max(0.0, self.committed_usd - usd), 2)
        self.realized_pnl = round(self.realized_pnl + pnl_usd, 2)


def _load_seen_addrs():
    seen = set()
    if os.path.exists(SHADOW_LOG):
        with open(SHADOW_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("event") in ("open", "close"):
                    seen.add((r.get("chain"), r.get("addr")))
    return seen


def _load_prior_equity_state():
    eq = _ShadowEquity(START_EQUITY_USD)
    if not os.path.exists(SHADOW_LOG):
        return eq
    open_stakes = {}
    with open(SHADOW_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            key = (r.get("chain"), r.get("addr"))
            if r.get("event") == "open":
                open_stakes[key] = r.get("stake_usd", 0.0)
                eq.committed_usd = round(eq.committed_usd + r.get("stake_usd", 0.0), 2)
            elif r.get("event") == "close":
                stk = open_stakes.pop(key, r.get("stake_usd", 0.0))
                eq.committed_usd = round(max(0.0, eq.committed_usd - stk), 2)
                eq.realized_pnl = round(eq.realized_pnl + r.get("pnl_usd", 0.0), 2)
    return eq


def _append_shadow(rec):
    os.makedirs(os.path.dirname(SHADOW_LOG), exist_ok=True)
    with open(SHADOW_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _new_candidates(since_ts, already_open_or_closed):
    out = []
    if not os.path.exists(CANDIDATE_LOG):
        return out
    with open(CANDIDATE_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("ts", 0) <= since_ts:
                continue
            if r.get("chain") not in ("robinhood_chain", "base"):
                continue
            if "compound" not in (r.get("lanes") or []):
                continue
            if r.get("mcap") is None:
                continue
            key = (r["chain"], r["addr"])
            if key in already_open_or_closed:
                continue
            out.append(r)
    return out


def _make_pos(cand, now, stake_usd):
    from executor.paper_ledger import _cost_pct
    entry_mcap = float(cand["mcap"])
    liq = cand.get("liq")
    return {
        "id": f"shadow:{cand['chain']}:{cand['addr']}:{int(now)}",
        "module": "shadow_compound", "chain": cand["chain"], "token": cand["addr"],
        "source": "shadow_sim", "signal": "shadow", "band": "n/a", "tags": {},
        "usd": stake_usd, "entry_mcap": entry_mcap, "peak_mcap": entry_mcap,
        "opened_ts": now, "remaining": 1.0, "price_trace": [], "breakeven_locked": False,
        "ladder_scale": 1.0, "rungs_fired": [], "proceeds_usd": 0.0,
        "cost_pct": _cost_pct(cand["chain"], stake_usd, liq),
        "liquidity_usd": liq, "unpriced_cycles": 0, "events": [], "strategy": "early",
        "tp_done": False, "exit_profile": "compound", "guard": "n/a", "live": False,
    }


def _fetch_mcaps(chain, slug, addrs):
    from multichain_sniper import _gt_get, parse_pools
    try:
        body = _gt_get(slug)
        if not body:
            return {}
        pools = parse_pools(body, chain)
        out = {}
        want = set(addrs)
        for p in pools:
            a = p.get("addr")
            mc = p.get("mcap")
            if a in want and mc:
                out[a] = mc
        return out
    except Exception:
        return {}


def _resolve_slugs():
    from multichain_sniper import resolve_rhc
    slugs = {"base": "base"}
    rhc = resolve_rhc()
    if rhc:
        slugs["robinhood_chain"] = rhc
    return slugs


def main():
    from executor.paper_ledger import _manage_early

    print("[shadow_compound_sim v2] starting -- continuous real-exit-ladder simulation "
          f"against a SHARED synthetic equity pool starting at ${START_EQUITY_USD:,.2f} "
          f"(shot_pct={SHOT_PCT}, max_share={MAX_SHARE}, matching real compound book sizing), "
          f"poll every {POLL_INTERVAL_S}s, writes ONLY to logs/shadow_compound_sim.jsonl, "
          "never touches real trading state.", flush=True)

    slugs = _resolve_slugs()
    print(f"[shadow_compound_sim] chain slugs resolved: {slugs}", flush=True)

    seen = _load_seen_addrs()
    eq_pool = _load_prior_equity_state()
    print(f"[shadow_compound_sim] equity pool restored: equity=${eq_pool.equity:,.2f} "
          f"committed=${eq_pool.committed_usd:,.2f} realized_pnl=${eq_pool.realized_pnl:+,.2f}",
          flush=True)
    open_positions = {}
    last_cand_ts = time.time() - 24 * 3600
    _last_slug_retry = time.time()

    while True:
        now = time.time()
        try:
            if "robinhood_chain" not in slugs and now - _last_slug_retry >= 120:
                _last_slug_retry = now
                try:
                    from multichain_sniper import resolve_rhc as _rr
                    _rhc = _rr()
                    if _rhc:
                        slugs["robinhood_chain"] = _rhc
                        print(f"[shadow_compound_sim] robinhood_chain slug resolved on retry: {_rhc}", flush=True)
                except Exception:
                    pass

            already = set(open_positions.keys()) | seen
            new_cands = _new_candidates(last_cand_ts, already)
            for cand in new_cands:
                last_cand_ts = max(last_cand_ts, cand["ts"])
                if cand["chain"] not in slugs:
                    continue
                stake = eq_pool.next_stake()
                if stake <= 0.0:
                    _append_shadow({"event": "skip_no_capital", "ts": now, "chain": cand["chain"],
                                     "addr": cand["addr"], "equity": eq_pool.equity,
                                     "committed_usd": eq_pool.committed_usd})
                    continue
                pos = _make_pos(cand, now, stake)
                key = (cand["chain"], cand["addr"])
                open_positions[key] = pos
                seen.add(key)
                eq_pool.open_stake(stake)
                _append_shadow({"event": "open", "ts": now, "chain": cand["chain"], "addr": cand["addr"],
                                 "entry_mcap": pos["entry_mcap"], "stake_usd": stake,
                                 "cost_pct": pos["cost_pct"], "equity_after_open": eq_pool.equity,
                                 "committed_after_open": eq_pool.committed_usd})
                print(f"[shadow_compound_sim] OPEN {cand['chain']} {cand['addr']} "
                      f"entry_mcap=${pos['entry_mcap']:,.0f} stake=${stake:,.2f} "
                      f"equity=${eq_pool.equity:,.2f} committed=${eq_pool.committed_usd:,.2f}",
                      flush=True)

            if open_positions:
                by_chain = {}
                for (chain, addr), pos in open_positions.items():
                    by_chain.setdefault(chain, []).append((addr, pos))

                for chain, items in by_chain.items():
                    slug = slugs.get(chain)
                    if not slug:
                        continue
                    addrs = [a for a, _ in items]
                    got = _fetch_mcaps(chain, slug, addrs)
                    for addr, pos in items:
                        mc = got.get(addr)
                        age_min = (now - pos["opened_ts"]) / 60.0
                        if mc is None:
                            if age_min >= MAX_HOLD_MIN:
                                stk = pos["usd"]
                                eq_pool.close_stake(stk, 0.0)
                                _append_shadow({"event": "close", "ts": now, "chain": chain, "addr": addr,
                                                 "exit_type": "no_price_data_timeout",
                                                 "reason": f"no price data after {age_min:.0f}min, giving up",
                                                 "entry_mcap": pos["entry_mcap"], "peak_mcap": pos["peak_mcap"],
                                                 "remaining": pos["remaining"], "proceeds_usd": pos["proceeds_usd"],
                                                 "stake_usd": stk, "pnl_usd": 0.0,
                                                 "equity_after_close": eq_pool.equity})
                                del open_positions[(chain, addr)]
                            continue
                        if mc > pos["peak_mcap"]:
                            pos["peak_mcap"] = mc
                        pos["price_trace"].append({"ts": now, "mult": round(mc / pos["entry_mcap"], 4)})
                        if len(pos["price_trace"]) > 400:
                            pos["price_trace"] = pos["price_trace"][-400:]
                        closed = _manage_early(pos, mc, now)
                        if not closed and age_min >= MAX_HOLD_MIN:
                            from executor.paper_ledger import _close
                            _close(pos, "max_hold_timeout", f"shadow sim max hold {MAX_HOLD_MIN}min reached", now)
                            closed = True
                        if closed:
                            stk = pos["usd"]
                            pnl = pos.get("pnl_usd", pos["proceeds_usd"] - stk)
                            eq_pool.close_stake(stk, pnl)
                            _append_shadow({"event": "close", "ts": now, "chain": chain, "addr": addr,
                                             "entry_mcap": pos["entry_mcap"], "peak_mcap": pos["peak_mcap"],
                                             "last_mcap": mc, "peak_mult": round(pos["peak_mcap"] / pos["entry_mcap"], 3),
                                             "exit_type": pos.get("exit_type"),
                                             "rungs_fired": pos["rungs_fired"], "proceeds_usd": round(pos["proceeds_usd"], 2),
                                             "stake_usd": stk, "pnl_usd": round(pnl, 2),
                                             "win": pos.get("win", pnl > 0), "hold_min": round(age_min, 1),
                                             "equity_after_close": eq_pool.equity,
                                             "committed_after_close": eq_pool.committed_usd})
                            print(f"[shadow_compound_sim] CLOSE {chain} {addr} exit={pos.get('exit_type')} "
                                  f"stake=${stk:,.2f} pnl=${pnl:+.2f} peak={pos['peak_mcap']/pos['entry_mcap']:.2f}x "
                                  f"rungs={pos['rungs_fired']} equity=${eq_pool.equity:,.2f}",
                                  flush=True)
                            del open_positions[(chain, addr)]
        except Exception as exc:
            import traceback
            traceback.print_exc()
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
