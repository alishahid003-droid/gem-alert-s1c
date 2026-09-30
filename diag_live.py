"""
Read-only live diagnostic -- run by .github/workflows/diag-live.yml with the
repo's real Secrets, or locally with your .env (`python diag_live.py`).

Answers, from REAL data instead of guesses (Sept 30 2026):
  1. Which keys are configured (presence only -- values are never printed).
  2. Is each runner actually running? (poll-fast / poll-slow on GitHub
     Actions, --poll-madeonsol on your PC) -- from the heartbeats every
     cycle now writes to state.
  3. Fomo (fomoapi.io): what /v2/alerts really returns (field names, time
     span of one page, alert types), which traders match the 38-name
     roster and which don't, whether leaderboard display names map roster
     names to real handles, whether paging/filter params work, and any
     credit/rate-limit headers.
  4. Would the current alerts have been auto-bought? -- the recorded
     Stage 1/2 verdicts, open/closed positions, budget and concurrency.

Writes NOTHING to state and spends no MadeOnSol budget. fomoapi.io calls
cost a handful of credits.
"""
import os
import sys
import time
from collections import Counter

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import CONFIG  # noqa: E402
import state  # noqa: E402
from utils.http import get_json  # noqa: E402
from layers.roster import SELL_WATCH_ROSTER  # noqa: E402

KEYS = [
    "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "MADEONSOL_API_KEY", "MOBULA_API_KEY",
    "FOMOAPI_API_KEY", "BIRDEYE_API_KEY", "GOPLUS_API_KEY", "ADANOS_API_KEY", "CRYPTOPANIC_AUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_CALLER_BOT_TOKEN", "TELEGRAM_CALLER_CHANNEL_IDS",
    "WALLET_ADDRESSES", "EXECUTION_ENABLED", "EXECUTION_SOLANA_PRIVATE_KEY", "EXECUTION_BSC_PRIVATE_KEY",
    "EXECUTION_RHC_PRIVATE_KEY", "RHC_RPC_URL", "TOTAL_WALLET_USD",
]


def hdr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _norm(s):
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _ago(ts):
    if not ts:
        return "never"
    d = time.time() - float(ts)
    if d < 3600:
        return f"{d / 60:.0f}m ago"
    if d < 86400:
        return f"{d / 3600:.1f}h ago"
    return f"{d / 86400:.1f}d ago"


def section_keys():
    hdr("1. KEYS CONFIGURED (presence only)")
    for k in KEYS:
        v = os.environ.get(k)
        shown = "SET" if v else "missing"
        if k == "EXECUTION_ENABLED" and v:
            shown = f"SET ({'true' if v == 'true' else 'not true'})"
        print(f"  {k:32s} {shown}")
    print(f"  state backend: {state.backend()}")


def section_heartbeats():
    hdr("2. RUNNER HEARTBEATS (is each scheduled job actually running?)")
    beats = state.get_runner_heartbeats() if hasattr(state, "get_runner_heartbeats") else {}
    if not beats:
        print("  no heartbeats recorded yet (added Sept 30 -- appear after the next cycle of each runner)")
    for name, b in sorted((beats or {}).items()):
        print(f"  {name:22s} last cycle {_ago(b.get('ts'))}  ({b.get('where', '?')}) {b.get('note', '')}")
    last13 = state.get_fomo_last_run()
    print(f"  layer13 last run: {_ago((last13 or {}).get('ts'))}  {last13 or ''}")
    print(f"  fomo alerts cursor: {state.get_fomo_alerts_since()}")
    used = state.madeonsol_calls_today()
    print(f"  MadeOnSol calls today (UTC day): {used} used, "
          f"{state.madeonsol_budget_remaining()} left of {state.MADEONSOL_DAILY_BUDGET} "
          f"(resets 00:00 UTC = 5:00 AM PKT)")


def _fomo(path, params=None):
    t0 = time.time()
    r = get_json(f"{CONFIG.fomoapi_base_url}{path}",
                 headers={"authorization": f"Bearer {CONFIG.fomoapi_api_key}"},
                 params=params or {}, timeout=CONFIG.http_timeout_seconds)
    r["_secs"] = round(time.time() - t0, 2)
    return r


def _ts(a):
    from layers.layer13_fomo_copytrade import alert_ts_seconds
    return alert_ts_seconds(a)


def section_fomo():
    hdr("3. FOMO (fomoapi.io) -- why roster buys/theses show zero")
    if not CONFIG.fomoapi_ready():
        print("  FOMOAPI_API_KEY missing in this environment -- cannot probe.")
        return
    r = _fomo("/v2/alerts", {"limit": 100})
    print(f"  /v2/alerts?limit=100 -> HTTP {r.get('status_code')} in {r['_secs']}s ok={r.get('ok')}")
    hdrs = r.get("headers") or {}
    for k, v in hdrs.items():
        if any(w in k.lower() for w in ("credit", "ratelimit", "rate-limit", "quota", "remaining")):
            print(f"    header {k}: {v}")
    if not r.get("ok"):
        body = r.get("text") or r.get("json")
        print(f"    body: {str(body)[:400]}")
        return
    body = r.get("json") or {}
    alerts = body.get("alerts") if isinstance(body, dict) else body
    alerts = alerts or []
    if isinstance(body, dict):
        print(f"    top-level keys: {sorted(body.keys())}")
    print(f"    alerts returned: {len(alerts)}")
    if not alerts:
        return
    print(f"    fields on one alert: {sorted(alerts[0].keys())}")
    sample = {k: v for k, v in alerts[0].items() if not isinstance(v, (dict, list))}
    print(f"    sample alert (scalars): {str(sample)[:500]}")
    stamps = [t for t in (_ts(a) for a in alerts) if t]
    if stamps:
        span = max(stamps) - min(stamps)
        print(f"    time span of this page: {span / 60:.1f} min (newest {_ago(max(stamps))})")
        print("    -> if this span is shorter than the poll interval, alerts are being MISSED between polls")
    types = Counter((a.get("alertType") or a.get("type") or "?") for a in alerts)
    print(f"    alert types: {dict(types)}")
    chains = Counter((a.get("chain") or a.get("network") or "?") for a in alerts)
    print(f"    chains: {dict(chains)}")

    roster_norm = {_norm(n): n for n in SELL_WATCH_ROSTER}
    traders = Counter((a.get("trader") or a.get("handle") or "?") for a in alerts)
    matched = {t: c for t, c in traders.items() if _norm(t) in roster_norm}
    print(f"    distinct traders on page: {len(traders)}; roster matches (normalized): {matched or 'NONE'}")
    print(f"    top traders on page: {traders.most_common(12)}")

    # paging / filter probes
    oldest_iso = None
    if stamps:
        import datetime
        oldest_iso = datetime.datetime.fromtimestamp(min(stamps), tz=datetime.timezone.utc).isoformat()
    first_id = alerts[0].get("id")
    for label, params in (("before=<oldest>", {"limit": 100, "before": oldest_iso}),
                          ("type=buy", {"limit": 100, "type": "buy"}),
                          (f"trader={traders.most_common(1)[0][0]}", {"limit": 20, "trader": traders.most_common(1)[0][0]})):
        if params.get("before") is None and "before" in params:
            continue
        p = _fomo("/v2/alerts", params)
        pa = ((p.get("json") or {}).get("alerts") if isinstance(p.get("json"), dict) else p.get("json")) or []
        same_first = bool(pa) and pa[0].get("id") == first_id and first_id is not None
        pt = Counter((a.get("trader") or "?") for a in pa)
        pty = Counter((a.get("alertType") or "?") for a in pa)
        pst = [t for t in (_ts(a) for a in pa) if t]
        print(f"    probe {label}: HTTP {p.get('status_code')} n={len(pa)} same_first_as_default={same_first} "
              f"types={dict(pty)} distinct_traders={len(pt)} "
              f"newest={_ago(max(pst)) if pst else '-'}")

    # leaderboard -> handle learning
    for window in ("7d", "30d"):
        lb = _fomo(f"/v2/leaderboard/{window}", {"limit": 150})
        rows = ((lb.get("json") or {}).get("traders") or []) if lb.get("ok") else []
        print(f"  /v2/leaderboard/{window}?limit=150 -> HTTP {lb.get('status_code')} rows={len(rows)}")
        if rows:
            print(f"    fields on one row: {sorted(rows[0].keys())}")
            hits = []
            for row in rows:
                h, d = row.get("handle"), row.get("displayName")
                name = roster_norm.get(_norm(h)) or roster_norm.get(_norm(d))
                if name:
                    hits.append(f"{name} -> handle '{h}'")
            print(f"    roster names found on {window} leaderboard ({len(hits)}): {hits}")

    # profile shape (wallet fields for the insider check)
    probe_handle = next(iter(matched), None) or traders.most_common(1)[0][0]
    prof = _fomo(f"/v2/users/{probe_handle}")
    pj = prof.get("json") or {}
    print(f"  /v2/users/{probe_handle} -> HTTP {prof.get('status_code')} keys={sorted(pj.keys()) if isinstance(pj, dict) else type(pj)}")
    if isinstance(pj, dict):
        for k, v in pj.items():
            if isinstance(v, (dict, list)):
                print(f"    {k}: {str(v)[:300]}")


def section_execution():
    hdr("4. WOULD THE CURRENT ALERTS HAVE BEEN AUTO-BOUGHT?")
    from executor.config import EXECUTOR_CONFIG
    import executor.position_state as position_state
    import executor.triggers as triggers
    info = EXECUTOR_CONFIG.bankroll_tier_info
    print(f"  wallet ${EXECUTOR_CONFIG.total_wallet_usd:.2f} tier={info['tier']} "
          f"stage1 size ${EXECUTOR_CONFIG.stage1_position_usd:.2f} (x band multiplier) "
          f"stage1 budget ${EXECUTOR_CONFIG.stage1_budget_usd():.2f} max_concurrent={info['max_concurrent']}")
    print(f"  execution enabled: {EXECUTOR_CONFIG.execution_enabled}")
    opens = position_state.list_open_positions()
    closed = position_state.list_closed_positions(limit=500)
    real_open = triggers.open_position_count()
    failed = sum(1 for p in opens if all(s.get("buy_status") == "failed" for s in p.get("stages", {}).values()))
    print(f"  open position records: {len(opens)} (holding money: {real_open}, all-buys-failed: {failed}); "
          f"closed: {len(closed)}")
    by_chain = Counter(p.get("chain") for p in opens)
    print(f"  open records by chain: {dict(by_chain)}")
    import datetime
    for p in opens:
        opened = datetime.datetime.fromtimestamp(p.get("opened_ts", 0), tz=datetime.timezone.utc).isoformat()
        print(f"    OPEN {p.get('chain')} {p.get('token')} opened {opened} total_usd={p.get('total_usd')} "
              f"filled_tokens={p.get('filled_amount_tokens')} tx={str(p.get('tx_signature'))[:20]}")
        for name, st in (p.get("stages") or {}).items():
            print(f"      {name}: usd={st.get('usd_amount')} buy_status={st.get('buy_status')} "
                  f"reason={st.get('reason')} fail={st.get('fail_reason')}")
    log = state.get_trade_log(limit=20)
    print(f"  trade log entries (latest 20): {len(log)}")
    for t in log[:10]:
        print(f"    {str({k: v for k, v in t.items() if k not in ('raw',)})[:220]}")
    feed = state.get_alert_feed(limit=150)
    ab = []
    for item in feed:
        tag = str((item.get("tags") or {}).get("Score", ""))
        if "band A" in tag or "band B" in tag:
            ab.append(item)
    print(f"  band A/B alerts in feed: {len(ab)}")
    verdicts = state.get_autobuy_verdicts([a.get("token_address") for a in ab])
    outcome = Counter()
    for a in ab[:40]:
        tok = a.get("token_address")
        v = verdicts.get(tok)
        chain = (a.get("tags") or {}).get("Chain", "?")
        if v:
            verdict = ("WOULD BUY ${:.2f}".format(v.get("position_usd") or 0) if v.get("fired")
                       else f"NO: {v.get('reason')}")
            if v.get("fired") and v.get("buy_reason"):
                verdict += f" | buy attempt: {v.get('buy_reason')}"
        else:
            verdict = "no verdict recorded (alert predates Sept 30 fix)"
        outcome[verdict.split(':')[0].split(' $')[0]] += 1
        print(f"    {str(tok)[:10]:10s} {chain:10s} {str((a.get('tags') or {}).get('Score', ''))[:22]:22s} {verdict[:110]}")
    print(f"  summary: {dict(outcome)}")
    fomo = state.get_fomo_signal_feed(limit=300)
    print(f"  fomo roster signals stored: {len(fomo)} "
          f"(buys {sum(1 for s in fomo if s.get('kind') == 'buy')}, "
          f"theses {sum(1 for s in fomo if s.get('kind') == 'thesis')}); "
          f"candidates: {len(state.get_fomo_candidates(limit=500))}; "
          f"promoted: {len(state.get_fomo_promoted())}")
    un = state.get_fomo_unmatched_handles()
    if un:
        top = sorted(un.items(), key=lambda kv: -kv[1].get("count", 0))[:15]
        print(f"  top unmatched fomo traders: {[(h, v.get('count')) for h, v in top]}")


def main():
    section_keys()
    for fn in (section_heartbeats, section_fomo, section_execution):
        try:
            fn()
        except Exception as e:  # diagnostic: report and keep going
            print(f"  !! {fn.__name__} failed: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
