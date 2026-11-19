"""Shadow compound simulator (19 Nov 2026, Ali's explicit ask): fills the real gap in
winners_tracker.py's +3h/+24h/+72h coarse revisit cadence by running a CONTINUOUS,
short-interval simulated walk of compound's real exit ladder against real live price data,
for every real candidate that qualifies for compound's lane on robinhood_chain/base.

SAFETY (Ali's explicit, standing constraint -- "the paper trade or anything should not
overlap with the real trading and creating a mess and a havoc for us"):
  - This script NEVER imports or calls executor/books.py's real stake/equity/can_open
    functions, NEVER touches state.py's shared state store, NEVER calls executor/
    paper_ledger.py's open_position()/manage() (which write to the real paper ledger
    sqlite/journal that feeds real equity() numbers compound and the cascade read).
  - It builds its OWN, fully synthetic, in-memory `pos` dict matching the shape
    executor/paper_ledger.py's _manage_early() expects, and calls ONLY that one pure
    function (it reads/mutates the dict it's given and nothing else -- confirmed by
    reading its source: no state.*, no books.*, no file writes inside _manage_early or
    the _sell/_close helpers it calls, other than appending to the dict and to the
    module-level in-memory CLOSED/legacy list IF ledger.open_position() was used to
    create it -- which this script deliberately never calls).
  - Writes ONLY to its own new file, logs/shadow_compound_sim.jsonl -- never touches
    logs/candidate_eval.jsonl (read-only source), logs/winners_book.json, or any real
    trading log.
  - Read-only against candidate_eval.jsonl: only reads it, to find candidates; never
    writes to it.
  - Uses the SAME shared API budget gate (api_budget.gate, via utils.http.get_json) every
    other real poller in this codebase uses, so it adds load within the existing shared
    budget rather than a separate, unthrottled one.

WHAT IT MEASURES: a candidate from candidate_eval.jsonl that qualified for the compound
lane on robinhood_chain/base is "opened" here as a synthetic $100 position at its real
logged entry mcap, then polled at a short interval against real GeckoTerminal mcap data
and run through paper_ledger.py's REAL, UNMODIFIED compound exit ladder
(EARLY_RULES["compound"] = ({1.3: 0.15, 2: 0.10, 3: 0.10}, 0.70, 0.78)) until it fully
closes (every rung sold, hard stop, no-pop stop, or max-hold) or SHADOW_MAX_HOLD_MIN
elapses. The realized P&L on that synthetic $100 is what the real trade would have made,
IF the real wallet had held the balance to take it and IF this walk's REAL_POLL_INTERVAL_S
granularity didn't miss a faster intra-interval move (an honest residual gap vs a true
tick-by-tick feed -- real cost/liquidity-slippage frictions ARE modeled via the same
_cost_pct() paper_ledger.py already applies to every real paper trade).

Usage: python shadow_compound_sim.py   (runs forever, Ctrl-C to stop; safe to run
alongside the live bot -- shares the same API budget gate, writes its own log only.)
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
SYNTHETIC_STAKE_USD = float(os.environ.get("SHADOW_STAKE_USD", "100"))


def _load_seen_addrs():
    """Addresses already opened in our own shadow log -- never re-open the same candidate."""
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


def _make_pos(cand, now):
    """Synthetic position dict -- same shape executor/paper_ledger.py's _manage_early()
    expects, built independently here (NOT via ledger.open_position(), so nothing is
    written to the real paper ledger)."""
    from executor.paper_ledger import _cost_pct
    entry_mcap = float(cand["mcap"])
    liq = cand.get("liq")
    return {
        "id": f"shadow:{cand['chain']}:{cand['addr']}:{int(now)}",
        "module": "shadow_compound", "chain": cand["chain"], "token": cand["addr"],
        "source": "shadow_sim", "signal": "shadow", "band": "n/a", "tags": {},
        "usd": SYNTHETIC_STAKE_USD, "entry_mcap": entry_mcap, "peak_mcap": entry_mcap,
        "opened_ts": now, "remaining": 1.0, "price_trace": [], "breakeven_locked": False,
        "ladder_scale": 1.0, "rungs_fired": [], "proceeds_usd": 0.0,
        "cost_pct": _cost_pct(cand["chain"], SYNTHETIC_STAKE_USD, liq),
        "liquidity_usd": liq, "unpriced_cycles": 0, "events": [], "strategy": "early",
        "tp_done": False, "exit_profile": "compound", "guard": "n/a", "live": False,
    }


def _fetch_mcaps(chain, slug, addrs):
    """19 Nov 2026 (bug fix, Ali caught it): originally used winners_tracker.gt_multi_fetch,
    which hits GeckoTerminal's /tokens/multi/ endpoint (token-level index). For a token only
    minutes old, that endpoint is frequently stale/unindexed and returned mcap values wildly
    below the real pool price -- 5/5 early closes all hit the hard stop on the FIRST poll,
    hold_min=0.0, peak=1.00x, which is a data-source bug, not real price action (confirmed:
    independent unrelated tokens do not crash identically in the same second).

    Fixed to use the exact same /new_pools endpoint (via _gt_get + parse_pools) the REAL live
    bot itself uses for every real entry mcap in candidate_eval.jsonl -- so this is now the
    same data source on both sides of the comparison, not two different GeckoTerminal indexes.
    /new_pools returns the most recent pools for the chain; we match our open addresses
    against whatever is in that response right now. A token that has aged out of the
    new-pools window (no longer among the most recent) will simply return no match this
    cycle -- handled by the existing no-price-data timeout path below, not a wrong price."""
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

    print("[shadow_compound_sim] starting -- continuous real-exit-ladder simulation, "
          f"poll every {POLL_INTERVAL_S}s, synthetic ${SYNTHETIC_STAKE_USD} stake per candidate, "
          "writes ONLY to logs/shadow_compound_sim.jsonl, never touches real trading state.",
          flush=True)

    slugs = _resolve_slugs()
    print(f"[shadow_compound_sim] chain slugs resolved: {slugs}", flush=True)

    seen = _load_seen_addrs()
    open_positions = {}  # (chain, addr) -> pos dict
    last_cand_ts = time.time() - 24 * 3600  # look back 24h on first run to backfill today's candidates
    _last_slug_retry = time.time()

    while True:
        now = time.time()
        try:
            # 19 Nov 2026 (bug fix, Ali caught it): resolve_rhc() was only called once at
            # startup -- one transient failure there meant robinhood_chain (~95% of our real
            # candidate volume) was silently skipped for the ENTIRE run with no recovery (the
            # real live bot's own local_runner.log showed robinhood_chain resolving fine and
            # continuously the whole time, confirming it was a one-off blip on this sim's side,
            # not a real outage). Retry every 120s whenever robinhood_chain is still missing.
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
                pos = _make_pos(cand, now)
                key = (cand["chain"], cand["addr"])
                open_positions[key] = pos
                seen.add(key)
                _append_shadow({"event": "open", "ts": now, "chain": cand["chain"], "addr": cand["addr"],
                                 "entry_mcap": pos["entry_mcap"], "stake_usd": SYNTHETIC_STAKE_USD,
                                 "cost_pct": pos["cost_pct"]})
                print(f"[shadow_compound_sim] OPEN {cand['chain']} {cand['addr']} entry_mcap=${pos['entry_mcap']:,.0f}",
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
                                _append_shadow({"event": "close", "ts": now, "chain": chain, "addr": addr,
                                                 "exit_type": "no_price_data_timeout",
                                                 "reason": f"no price data after {age_min:.0f}min, giving up",
                                                 "entry_mcap": pos["entry_mcap"], "peak_mcap": pos["peak_mcap"],
                                                 "remaining": pos["remaining"], "proceeds_usd": pos["proceeds_usd"],
                                                 "stake_usd": SYNTHETIC_STAKE_USD})
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
                            # pos["pnl_usd"]/pos["win"]/pos["exit_type"] are set by _close() itself
                            # (called either inside _manage_early() via its own _close call, or by
                            # us above for the max-hold-timeout path) -- read them back rather than
                            # recomputing, so this always matches the exact real exit-engine math.
                            pnl = pos.get("pnl_usd", pos["proceeds_usd"] - SYNTHETIC_STAKE_USD)
                            _append_shadow({"event": "close", "ts": now, "chain": chain, "addr": addr,
                                             "entry_mcap": pos["entry_mcap"], "peak_mcap": pos["peak_mcap"],
                                             "last_mcap": mc, "peak_mult": round(pos["peak_mcap"] / pos["entry_mcap"], 3),
                                             "exit_type": pos.get("exit_type"),
                                             "rungs_fired": pos["rungs_fired"], "proceeds_usd": round(pos["proceeds_usd"], 2),
                                             "stake_usd": SYNTHETIC_STAKE_USD, "pnl_usd": round(pnl, 2),
                                             "win": pos.get("win", pnl > 0), "hold_min": round(age_min, 1)})
                            print(f"[shadow_compound_sim] CLOSE {chain} {addr} exit={pos.get('exit_type')} pnl=${pnl:+.2f} "
                                  f"peak={pos['peak_mcap']/pos['entry_mcap']:.2f}x rungs={pos['rungs_fired']}",
                                  flush=True)
                            del open_positions[(chain, addr)]
        except Exception as exc:  # noqa: BLE001 -- never let the sim loop die silently
            import traceback
            traceback.print_exc()
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
