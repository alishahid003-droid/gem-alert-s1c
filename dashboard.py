"""
Local dashboard -- comprehensive live view of every S1c module, alert feed,
and open positions (Ali, Sept 23 2026: "the dashboard needs to be created.
We are going to deploy this into our laptop where the dashboard screen
should show every module... how it's working, alert generated from there
plus an alert on telegram as well... the entire view of the positions open
or what alerts are coming").

WHAT THIS IS: a single local web page, served from your own laptop, reading
the SAME state backend the GitHub Actions poll cycles already write to --
Upstash Redis if UPSTASH_REDIS_REST_URL/TOKEN are set in your .env (same as
scheduler.py uses), else the local .gem_alert_state.json file. If Upstash
is configured (it should be, since GitHub Actions runs are stateless and
need a shared backend across runs), this dashboard shows REAL, LIVE data
from the actual poll cycles running on their schedule -- not a simulation,
and nothing here triggers a poll cycle itself.

Deliberately built with ZERO new dependencies (stdlib http.server only,
no Flask/FastAPI) so it runs on your laptop with nothing to install beyond
what scheduler.py already needs -- just:

    python dashboard.py

...then open http://localhost:8787 in a browser. Auto-refreshes every 10s
via a small JS poll against /api/data (no full-page reload). Read-only:
this cannot send alerts, open/close positions, or change any config --
Telegram keeps being the live-alert channel exactly as before; this is a
second, parallel view onto the same data, not a replacement.

REWRITTEN Sept 29 2026 (Ali, live, direct feedback: "use your brain...
not these square boxes...should be a good user interface giving me an
exact picture. Plus an exact picture of my open positions. Exact picture
of my profit and loss on those open positions... if you have different
bands, prioritize the amount accordingly... if some coins fall in category
D... you should not even show them then"). Three real changes from the
Sept 24 version, none of them cosmetic-only:

1. OPEN POSITIONS NOW SHOW LIVE UNREALIZED P&L, not just what was staked.
   Each open position gets one fresh DexScreener snapshot per dashboard
   refresh (same fetch_dexscreener_snapshot already proven live in
   scheduler.py's _run_position_management_cycle -- not a new API, not a
   new risk), and current value / P&L $ / P&L % are computed the same way
   campaign_milestones.position_value_usd already does (entry_mcap vs
   current_mcap_usd, scaled by remaining_pct so an already-trimmed moonbag
   isn't double-counted). A short-lived server-side cache (15s) keeps the
   browser's 10s auto-poll from hammering DexScreener on every tick -- the
   browser polls every 10s, the underlying price data only refreshes at
   most every 15s.

2. BAND-C/D STRUCTURAL-SCORE-ONLY ALERTS ARE HIDDEN BY DEFAULT. These are
   the ones Ali called noise -- a token that only ever showed up because
   Layer 0/0b's structural score landed low, with no deployer/convergence/
   news signal attached, is not something worth scrolling past by default
   (and per executor/triggers.py, band C/D alone never fires a real buy
   either -- see stage1_position_usd_for_band's docstring). A real alert
   that ALSO carries a deployer tier, wallet convergence, news hit,
   backing/buzz tag, or an actual Stage 1/2 fire is never hidden, whatever
   its band -- those are actionable regardless of the structural score.
   A "Show all (incl. band C/D noise)" toggle reveals everything, off by
   default, for whenever Ali actually wants to see what got filtered
   (e.g. checking whether a real rug got flagged and suppressed).

3. THE 32-CARD MODULE GRID THAT USED TO DOMINATE THE PAGE IS NOW ONE
   COLLAPSED SUMMARY LINE ("24/32 modules ready") with a toggle to expand
   the full per-module detail -- collapsed by default, so the page opens
   on what actually matters (positions, P&L, alerts) instead of forcing a
   scroll past 32 boxes to get there.

Positions/pnl/alerts/trades stay READ-ONLY exactly as before: this cannot
send alerts, open/close positions, or change any config.
"""
import json
import threading
import time
import concurrent.futures
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Local-only auto-load of a .env file (Sept 29 2026 -- real bug found and
# fixed: scheduler.py/worker_pulse_websocket.py/backtest.py all call
# load_dotenv() before importing config/state, but dashboard.py never did,
# despite reading the exact same os.environ-backed CONFIG/state module.
# This is the actual root cause of dashboard.py showing "state backend:
# local_json" instead of "upstash" -- not which CMD window it's run from,
# the script itself just never loaded .env. Must happen BEFORE `import
# state` below, same ordering reason as scheduler.py's own comment: config.py
# builds CONFIG from os.environ at import time, so loading .env any later
# leaves CONFIG holding stale/missing values. No-op if python-dotenv isn't
# installed or no .env file exists.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import state
import links
from scheduler import readiness_report
import executor.position_state as position_state
import executor.compound_scalper as compound_scalper
import executor.paper_ledger as paper_ledger
from layers.layer0_scoring import fetch_dexscreener_snapshot

PORT = 8787

# Server-side cache for live price snapshots -- keeps the browser's 10s
# auto-poll from firing a fresh DexScreener call per open position on
# every single tick. {(chain, token): (fetched_at_ts, snapshot_or_None)}
_SNAPSHOT_CACHE: dict = {}
_SNAPSHOT_CACHE_TTL_SECONDS = 15


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "?"


def _ago(ts: float) -> str:
    try:
        secs = time.time() - ts
    except Exception:
        return "?"
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs / 60)}m ago"
    if secs < 86400:
        return f"{int(secs / 3600)}h ago"
    return f"{int(secs / 86400)}d ago"


# Which alert-feed tags map to which visual category, so the feed can be
# filtered/colored by "what kind of alert is this" the way Ali asked
# ("gem alert or rug alert or developer alert or copy trading alert").
LAYER_CATEGORY = {
    "layer0": "gem", "layer0b": "gem", "layer0c": "gem", "layer0c_momentum": "gem",
    "layer1": "developer", "layer2": "copy-trading", "layer2_single": "copy-trading", "layer2_untracked_large": "insider-watch", "layer2b": "copy-trading", "layer2b_single": "copy-trading", "layer9": "sell/rug-watch",
    "layer4_news": "news", "layer7": "correlation", "layer3": "backing", "layer11": "buzz",
    "post_alert_downgrade": "sell/rug-watch",
}

# Only "gem" category alerts (Layer 0/0b/0c -- pure structural-score scans)
# get band-filtered. Every other category (deployer, copy-trading, news,
# correlation, backing, buzz, rug-watch) carries its own real signal beyond
# the structural score, so it's never hidden by band.
BAND_FILTERABLE_CATEGORIES = {"gem"}


def _extract_band(tags: dict):
    """Band rides inside the free-text 'Score' tag, e.g. '46/100 (band C)'
    -- see scheduler.py's alert.set_tag("Score", ...) call sites. No
    separate structured band field exists on an alert entry today; parsed
    here rather than changing the stored shape (see log_full_alert)."""
    score_tag = (tags or {}).get("Score", "")
    if "band " in score_tag:
        tail = score_tag.split("band ", 1)[1]
        band = tail[0].upper()
        if band in ("A", "B", "C", "D"):
            return band
    return None


def _get_cached_snapshot(chain: str, token: str):
    key = (chain, token)
    cached = _SNAPSHOT_CACHE.get(key)
    now = time.time()
    if cached and (now - cached[0]) < _SNAPSHOT_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        snap = fetch_dexscreener_snapshot(chain, token)
    except Exception:
        snap = None
    _SNAPSHOT_CACHE[key] = (now, snap)
    return snap


def _entry_mcap(pos: dict):
    """Same rule as moonbag._entry_mcap / campaign_milestones._entry_mcap
    -- duplicated (not imported) on purpose, same reasoning as
    campaign_milestones.py's own docstring: it's three lines, and this
    view module staying readable standalone is worth more than saving an
    import. All three copies must stay in sync if this rule ever changes."""
    stages = pos.get("stages", {})
    if "stage1" in stages and stages["stage1"].get("entry_mcap"):
        return stages["stage1"]["entry_mcap"]
    if "stage2" in stages and stages["stage2"].get("entry_mcap"):
        return stages["stage2"]["entry_mcap"]
    return None


def _execution_status(pos: dict) -> tuple:
    """Sept 30 2026 (Ali: "i have not placed my keys neither have i placed
    funds in the wallet then how come those 2 came" -- a fair, important
    question): position_state.record_stage_entry() logs a position the
    MOMENT a trigger decides to buy, BEFORE the real swap is even
    attempted (see executor/entrypoint.py's own docstring) -- so a
    position with a real dollar amount staked can still mean the real buy
    never went through (no wallet key configured, EXECUTION_ENABLED off,
    insufficient funds, a failed swap). The dashboard was showing
    "$X staked" identically whether real money moved or not. This computes
    which actually happened, from the same buy_status/fill_status fields
    executor/position_state.py already records but dashboard.py never
    read. Returns (status, human_detail):
      "real"    -- fill_status == "confirmed", tokens actually received
      "failed"  -- a stage's buy_status == "failed" -- NO real money spent,
                    despite the $ shown in "Staked $"
      "unconfirmed" -- a tx was sent but the filled amount couldn't be
                    parsed -- needs manual on-chain reconciliation
      "pending" -- no stage has resolved buy/fill yet (rare; usually means
                    a stage was just recorded this cycle and the buy
                    attempt hasn't been logged back yet)"""
    if pos.get("fill_status") == "confirmed" and (pos.get("amount_tokens") or 0) > 0:
        return "real", f"{pos['amount_tokens']:.4g} tokens received"
    failed_stages = [s for s, v in (pos.get("stages") or {}).items() if v.get("buy_status") == "failed"]
    if failed_stages:
        reasons = "; ".join(
            f"{s}: {(pos['stages'][s].get('fail_reason') or 'buy failed')}" for s in failed_stages
        )
        return "failed", f"NO real money moved -- {reasons}"
    if pos.get("fill_status") == "unconfirmed_amount":
        return "unconfirmed", "tx sent but fill amount unclear -- check on-chain manually"
    return "pending", "buy attempt not yet resolved"


def _with_live_pnl(pos: dict) -> dict:
    """Adds current_mcap_usd / value_usd / pnl_usd / pnl_pct / remaining_pct
    onto an open position dict for display. Any missing input (no
    entry_mcap on record, DexScreener can't currently price it) leaves
    those fields as None rather than guessing -- same fail-closed posture
    as campaign_milestones.position_value_usd, which this mirrors."""
    chain, token = pos.get("chain"), pos.get("token")
    entry_mcap = _entry_mcap(pos)
    snap = _get_cached_snapshot(chain, token) if chain and token else None
    current_mcap = snap.get("mcap_usd") if snap else None
    stake_usd = pos.get("total_usd", 0.0)
    remaining = position_state.remaining_pct(chain, token) if chain and token else 1.0

    value_usd = pnl_usd = pnl_pct = None
    if entry_mcap and current_mcap and entry_mcap > 0:
        multiple = current_mcap / entry_mcap
        value_usd = stake_usd * multiple * remaining
        cost_basis_remaining = stake_usd * remaining
        pnl_usd = value_usd - cost_basis_remaining
        pnl_pct = (multiple - 1) * 100

    pos["current_mcap_usd"] = current_mcap
    pos["value_usd"] = value_usd
    pos["pnl_usd"] = pnl_usd
    pos["pnl_pct"] = pnl_pct
    pos["remaining_pct"] = remaining
    pos["price_unavailable"] = current_mcap is None
    return pos


def _autobuy_text(v) -> dict:
    """{"level": ok|no|na, "text": ...} for the Alerts tab's Auto-buy column."""
    if not v:
        return {"level": "na", "text": "-"}
    if v.get("fired"):
        amt = v.get("position_usd") or 0
        if v.get("buy_ok"):
            return {"level": "ok", "text": f"BOUGHT ${amt:.2f}"}
        reason = v.get("buy_reason") or ""
        if "EXECUTION_ENABLED" in reason or "wallet key configured" in reason:
            return {"level": "ok", "text": f"WOULD BUY ${amt:.2f} (execution off / no key yet)"}
        return {"level": "no", "text": f"fired ${amt:.2f}, buy failed: {reason[:80]}"}
    return {"level": "no", "text": f"NO: {(v.get('reason') or '')[:90]}"}


def _health() -> dict:
    """System-tab health (Sept 30 2026): is each runner actually running,
    and what's the real fomoapi.io credit situation."""
    now = time.time()
    beats = state.get_runner_heartbeats()
    runners = []
    for name, expect_min in (("poll-fast", 30), ("poll-slow", 60), ("poll-madeonsol", 45)):
        b = beats.get(name) or {}
        ts = b.get("ts")
        age_min = (now - ts) / 60 if ts else None
        runners.append({"name": name, "ago": _ago(ts) if ts else "never",
                        "where": b.get("where") or "-", "note": b.get("note") or "",
                        "stale": age_min is None or age_min > expect_min})
    credits = state.get_fomo_credit_state()
    backoff = credits.get("backoff_until", 0) > now
    unmatched = state.get_fomo_unmatched_handles()
    top_unmatched = sorted(unmatched.items(), key=lambda kv: -kv[1].get("count", 0))[:10]
    return {
        "runners": runners,
        "fomo_credits_remaining": credits.get("remaining"),
        "fomo_spent_today": credits.get("spent_today") if credits.get("day") == time.strftime("%Y-%m-%d", time.gmtime()) else 0,
        "fomo_backoff": backoff,
        "fomo_last_run": state.get_fomo_last_run(),
        "fomo_unmatched": [{"handle": h, "count": v.get("count")} for h, v in top_unmatched],
        "fomo_promoted": list(state.get_fomo_promoted().values()),
        "madeonsol": state.madeonsol_pacing_status(),
    }


def build_data() -> dict:
    """Sept 30 2026 (Ali, "same pathetic interface and bullshit stuff" --
    /api/data was hanging indefinitely): every section below is timed and
    printed to this process's own console, so a future slowdown shows up
    immediately in the terminal running `python dashboard.py` instead of
    silently stalling the page. The open-position live-PnL enrichment
    (one DexScreener call per position) is also now run CONCURRENTLY with
    a hard per-call timeout, instead of sequentially with no ceiling --
    that, plus state.py's new pipelined batch-fetch (see
    position_state.list_open_positions/list_closed_positions), were the
    two real causes of the multi-minute hang: dozens of positions x
    sequential Upstash calls x sequential DexScreener calls, each subject
    to utils/http.py's own retry/backoff stack."""
    t0 = time.time()

    def _lap(label):
        print(f"[dashboard] build_data: {label} done at +{time.time() - t0:.2f}s")

    report = readiness_report()
    _lap("readiness_report")

    feed = state.get_alert_feed(limit=150)
    for item in feed:
        item["ago"] = _ago(item["ts"])
        item["ts_fmt"] = _fmt_ts(item["ts"])
        item["category"] = LAYER_CATEGORY.get(item.get("layer"), item.get("layer") or "other")
        item["band"] = _extract_band(item.get("tags"))
        item["noise"] = item["category"] in BAND_FILTERABLE_CATEGORIES and item["band"] in ("C", "D")
        item["links"] = links.build_links(item.get("chain"), item.get("token_address"))
    # Auto-buy verdict per alert (Sept 30 2026, Ali: "would these trades
    # have been executed?") -- the real Stage 1/2 trigger decision recorded
    # by scheduler._handle_scored, fetched in ONE batched read.
    verdicts = state.get_autobuy_verdicts([i.get("token_address") for i in feed if i.get("band") in ("A", "B")])
    for item in feed:
        item["autobuy"] = _autobuy_text(verdicts.get(item.get("token_address")))
    _lap("alert_feed")

    open_raw = position_state.list_open_positions()
    _lap(f"list_open_positions ({len(open_raw)} open)")
    if open_raw:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(open_raw))) as pool:
            futures = {pool.submit(_with_live_pnl, p): p for p in open_raw}
            positions = []
            for fut in futures:
                try:
                    positions.append(fut.result(timeout=10))
                except Exception:
                    # a single stuck/slow DexScreener call must never take
                    # the whole dashboard down with it -- show the position
                    # with price_unavailable=True instead.
                    p = futures[fut]
                    p["current_mcap_usd"] = None
                    p["value_usd"] = None
                    p["pnl_usd"] = None
                    p["pnl_pct"] = None
                    p["remaining_pct"] = 1.0
                    p["price_unavailable"] = True
                    positions.append(p)
    else:
        positions = []
    for p in positions:
        p["opened_fmt"] = _fmt_ts(p.get("opened_ts")) if p.get("opened_ts") else "?"
        p["execution_status"], p["execution_detail"] = _execution_status(p)
    _lap("open_positions live-pnl enrichment")

    closed_positions = position_state.list_closed_positions(limit=100)
    for p in closed_positions:
        p["opened_fmt"] = _fmt_ts(p.get("opened_ts")) if p.get("opened_ts") else "?"
        p["closed_fmt"] = _fmt_ts(p.get("closed_ts")) if p.get("closed_ts") else "?"
    _lap(f"closed_positions ({len(closed_positions)})")

    trade_log = state.get_trade_log(limit=200)
    _lap("trade_log")
    for t in trade_log:
        t["ago"] = _ago(t["ts"])
        t["ts_fmt"] = _fmt_ts(t["ts"])
        t["explorer"] = links.build_links(t.get("chain"), t.get("token")) if t.get("tx_signature") else {}

    # Layer 13 (Fomo copy-trading/thesis, Sept 30 2026) -- dashboard-only,
    # never sent to Telegram, see layers/layer13_fomo_copytrade.py.
    fomo_signals = state.get_fomo_signal_feed(limit=100)
    for f in fomo_signals:
        f["ago"] = _ago(f["ts"])
        f["ts_fmt"] = _fmt_ts(f["ts"])
        f["links"] = links.build_links(f.get("chain"), f.get("token_address")) if f.get("token_address") else {}
    fomo_candidates = state.get_fomo_candidates(limit=50)
    for c in fomo_candidates:
        c["ago"] = _ago(c["ts"])
    _lap("fomo_signals + fomo_candidates")

    modules = _flatten_readiness(report)
    unrealized_total = sum(p["pnl_usd"] for p in positions if p.get("pnl_usd") is not None)

    # Compound scalper pool (Ali, Sept 29 2026) -- entirely separate pool/
    # state from position_state.py above, see executor/compound_scalper.py's
    # own docstring for why. status() is read-only, never mutates.
    scalper_status = compound_scalper.status()
    if scalper_status.get("open_position"):
        scalper_status["open_position"]["opened_fmt"] = (
            _fmt_ts(scalper_status["open_position"].get("opened_ts"))
            if scalper_status["open_position"].get("opened_ts") else "?"
        )
    _lap("compound_scalper.status")

    realized = position_state.realized_pnl_summary()
    _lap("realized_pnl_summary")

    out = {
        "generated_at": _fmt_ts(time.time()),
        "readiness": report,
        "modules_ready_count": sum(1 for m in modules if m["ready"]),
        "modules_total_count": len(modules),
        "modules": modules,
        "alert_feed": feed,
        "open_positions": positions,
        "closed_positions": closed_positions,
        "trade_log": trade_log,
        "realized_pnl": realized,
        "unrealized_pnl_usd": unrealized_total,
        "state_backend": state.backend() if hasattr(state, "backend") else "?",
        "compound_scalper": scalper_status,
        "fomo_signals": fomo_signals,
        "fomo_candidates": fomo_candidates,
        "health": _health(),
        "paper": paper_ledger.scoreboard(),
        "win_rate_target": 0.8,
    }
    _lap(f"TOTAL")
    return out


def _flatten_readiness(r) -> list:
    rows = []

    def walk(obj):
        for k, v in obj.items():
            if isinstance(v, dict) and not isinstance(v, list):
                if "ready" in v:
                    rows.append({"name": k, "ready": bool(v.get("ready")), "note": v.get("note", "")})
                else:
                    walk(v)
            elif isinstance(v, bool):
                rows.append({"name": k, "ready": v, "note": ""})

    walk(r)
    return rows


PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>S1c Dashboard</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { background:#0b0e11; color:#e6e6e6; font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; padding:0 20px 40px; }
  h1 { font-size:20px; margin:20px 0 4px; }
  .sub { color:#8a8f98; font-size:12px; margin-bottom:18px; }
  .statbar { display:grid; grid-template-columns: repeat(auto-fit, minmax(160px,1fr)); gap:10px; margin-bottom:24px; }
  .stat { background:#161a1f; border:1px solid #2a2f37; border-radius:8px; padding:12px 14px; }
  .stat .label { color:#8a8f98; font-size:11px; text-transform:uppercase; letter-spacing:.03em; }
  .stat .value { font-size:22px; font-weight:600; margin-top:2px; }
  section { margin-bottom:26px; }
  section > h2 { font-size:14px; border-bottom:1px solid #2a2f37; padding-bottom:6px; margin-bottom:10px; display:flex; align-items:center; justify-content:space-between; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th, td { text-align:left; padding:7px 8px; border-bottom:1px solid #1c2026; vertical-align:top; }
  th { color:#8a8f98; font-weight:500; position:sticky; top:0; background:#0b0e11; }
  .tag { display:inline-block; background:#21252b; border-radius:4px; padding:1px 6px; margin:1px 2px 1px 0; font-size:11px; }
  .cat-gem { color:#2ecc71; } .cat-developer { color:#f1c40f; } .cat-copy-trading { color:#3498db; }
  .cat-sell\\/rug-watch { color:#e74c3c; } .cat-news { color:#9b59b6; } .cat-correlation { color:#e67e22; }
  .cat-backing { color:#1abc9c; } .cat-buzz { color:#ff6ec7; } .cat-insider-watch { color:#ff4757; font-weight:600; }
  .cat-buy { color:#3498db; } .cat-thesis { color:#ff6ec7; font-style:italic; }
.exec-real { color:#2ecc71; font-weight:600; font-size:11px; padding:2px 6px; border:1px solid #2ecc71; border-radius:3px; }
.exec-failed { color:#ff4757; font-weight:600; font-size:11px; padding:2px 6px; border:1px solid #ff4757; border-radius:3px; }
.exec-unconfirmed { color:#f39c12; font-weight:600; font-size:11px; padding:2px 6px; border:1px solid #f39c12; border-radius:3px; }
.exec-pending { color:#8a8f98; font-weight:600; font-size:11px; padding:2px 6px; border:1px solid #8a8f98; border-radius:3px; }
.exec-detail { color:#8a8f98; font-size:10px; margin-top:2px; max-width:220px; }
  .empty { color:#555; font-style:italic; padding:10px 4px; }
  .toggle { background:#161a1f; border:1px solid #2a2f37; color:#8a8f98; border-radius:5px; padding:4px 10px; font-size:11px; cursor:pointer; }
  .toggle.active { background:#2a2f37; color:#e6e6e6; }
  .linkbtn { display:inline-block; background:#1f3a2e; color:#2ecc71; border:1px solid #2a5c40; border-radius:4px; padding:2px 7px; margin:1px 3px 1px 0; font-size:11px; text-decoration:none; }
  .linkbtn:hover { background:#2a5c40; }
  .pos-pnl { color:#2ecc71; } .neg-pnl { color:#e74c3c; } .na { color:#555; }
  .side-buy { color:#2ecc71; font-weight:600; } .side-sell { color:#e67e22; font-weight:600; }
  .ok-yes { color:#2ecc71; } .ok-no { color:#e74c3c; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:11px; }
  .band-A { color:#2ecc71; font-weight:700; } .band-B { color:#3498db; font-weight:700; }
  .band-C { color:#f1c40f; } .band-D { color:#e74c3c; }
  .modules-summary { display:flex; align-items:center; gap:10px; cursor:pointer; user-select:none; }
  .modules-summary .dot { display:inline-block; width:9px; height:9px; border-radius:50%; }
  .modules-detail { display:none; margin-top:10px; }
  .modules-detail.open { display:grid; grid-template-columns: repeat(auto-fit, minmax(240px,1fr)); gap:10px; }
  .mcard { background:#161a1f; border:1px solid #2a2f37; border-radius:8px; padding:10px 12px; font-size:11px; }
  .mcard h4 { margin:0 0 4px; font-size:12px; display:flex; align-items:center; gap:6px; }
  .mcard .note { color:#8a8f98; }
  .mdot { display:inline-block; width:8px; height:8px; border-radius:50%; }
  .mdot.ready { background:#2ecc71; } .mdot.blocked { background:#e74c3c; }

  /* --- Sept 30 2026: tabbed, colorful, "what do I actually look at" redesign.
     Ali's own words: "not these square boxes... should be a good user
     interface giving me an exact picture... i really don't know what is
     happening or what to see in that list." Nothing about build_data() or
     the JSON shape changes below -- this only changes how the same data
     is laid out and colored: a top attention banner that says in plain
     English what needs eyes right now, and 5 tabs instead of one long
     scroll of every section stacked on top of each other. */
  .attn { border-radius:10px; padding:14px 16px; margin-bottom:20px; font-size:13px; }
  .attn-ok { background:#0f2417; border:1px solid #1f5c37; color:#6fe3a3; }
  .attn-warn { background:#2b230f; border:1px solid #6b5518; color:#ffcf5c; }
  .attn-bad { background:#2b1414; border:1px solid #6b1f1f; color:#ff8080; }
  .attn h3 { margin:0 0 6px; font-size:13px; text-transform:uppercase; letter-spacing:.03em; }
  .attn ul { margin:0; padding-left:18px; }
  .attn li { margin:3px 0; }
  .tabs { display:flex; gap:6px; margin-bottom:20px; border-bottom:1px solid #2a2f37; flex-wrap:wrap; }
  .tabbtn { background:none; border:none; color:#8a8f98; font-size:13px; font-weight:600; padding:10px 16px; cursor:pointer; border-bottom:2px solid transparent; }
  .tabbtn:hover { color:#e6e6e6; }
  .tabbtn.active { color:#e6e6e6; border-bottom:2px solid #3498db; }
  .tabbtn .badge { display:inline-block; background:#e74c3c; color:#fff; border-radius:9px; font-size:10px; font-weight:700; padding:1px 6px; margin-left:5px; }
  .tabpanel { display:none; }
  .tabpanel.active { display:block; }
  .highlight-list { display:flex; flex-direction:column; gap:6px; }
  .highlight-item { background:#161a1f; border:1px solid #2a2f37; border-radius:7px; padding:8px 12px; font-size:12px; display:flex; justify-content:space-between; gap:10px; align-items:center; }
  .highlight-item .hl-left { display:flex; gap:8px; align-items:center; }
  .stat.stat-good .value { color:#2ecc71; } .stat.stat-bad .value { color:#e74c3c; }
</style></head>
<body>
<h1>S1c &mdash; Fomo Gem-Alert System</h1>
<div class="sub" id="meta">loading...</div>

<div class="statbar" id="statbar"></div>
<div id="attention"></div>

<nav class="tabs" id="tabs">
  <button class="tabbtn active" data-tab="overview">Overview</button>
  <button class="tabbtn" data-tab="positions">Positions <span class="badge" id="badge-positions" style="display:none"></span></button>
  <button class="tabbtn" data-tab="alerts">Alerts <span class="badge" id="badge-alerts" style="display:none"></span></button>
  <button class="tabbtn" data-tab="fomo">Fomo</button>
  <button class="tabbtn" data-tab="system">System</button>
</nav>

<div class="tabpanel active" data-panel="overview">
  <section>
    <h2>What needs your eyes <span style="color:#8a8f98; font-weight:normal; font-size:12px;">(most recent, non-noise alerts)</span></h2>
    <div id="recent_highlights"></div>
  </section>
  <section>
    <h2>Compound Scalper <span class="tag" id="scalper-tag"></span></h2>
    <div id="scalper_detail"></div>
  </section>
</div>

<div class="tabpanel" data-panel="positions">
  <section>
    <h2>Win-Rate Scoreboard <span style="color:#8a8f98; font-weight:normal; font-size:12px;">(paper trades -- every signal, same exits as real money, costs included)</span></h2>
    <div id="paper"></div>
  </section>
  <section>
    <h2>Open Positions</h2>
    <div id="positions"></div>
  </section>

  <section>
    <h2>Closed Positions</h2>
    <div id="closed_positions"></div>
  </section>

  <section>
    <h2>Trade History</h2>
    <div id="trade_log"></div>
  </section>
</div>

<div class="tabpanel" data-panel="alerts">
  <section>
    <h2>
      Alerts
      <button class="toggle" id="noise-toggle">Show all (incl. band C/D noise)</button>
    </h2>
    <div id="feed"></div>
  </section>
</div>

<div class="tabpanel" data-panel="fomo">
  <section>
    <h2>Fomo Copy-Trading &amp; Theses <span style="color:#8a8f98; font-weight:normal; font-size:12px;">(tracked traders only -- never sent to Telegram)</span></h2>
    <div id="fomo_signals"></div>
  </section>

  <section>
    <h2>Fomo New-Trader Candidates <span style="color:#8a8f98; font-weight:normal; font-size:12px;">(&gt;= $5k balance, not on your roster -- auto-promoted when 7d AND 30d PnL are positive and not insider-flagged)</span></h2>
    <div id="fomo_candidates"></div>
  </section>
</div>

<div class="tabpanel" data-panel="system">
  <section>
    <h2>Health</h2>
    <div id="health"></div>
  </section>
  <section>
    <h2>Modules</h2>
    <div class="modules-summary" id="modules-summary"></div>
    <div class="modules-detail" id="modules-detail"></div>
  </section>
</div>

<script>
document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tabbtn");
  if (!btn) return;
  document.querySelectorAll(".tabbtn").forEach(b => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".tabpanel").forEach(p => p.classList.toggle("active", p.dataset.panel === btn.dataset.tab));
});

function esc(s) { return (s === undefined || s === null) ? "" : String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function fmtUsd(v) { return v == null ? '<span class="na">unpriced</span>' : (v >= 0 ? '<span class="pos-pnl">$' : '<span class="neg-pnl">-$') + Math.abs(v).toFixed(2) + '</span>'; }
function fmtPct(v) { return v == null ? '<span class="na">-</span>' : (v >= 0 ? '<span class="pos-pnl">+' : '<span class="neg-pnl">') + v.toFixed(1) + '%</span>'; }

let showNoise = false;
let modulesOpen = false;

/* Turns the same data render() already has into a single plain-English
   banner ("what needs your eyes right now") plus a short highlight list on
   the Overview tab, so Ali doesn't have to read every table to know
   whether anything actually needs him. Pure read of existing fields --
   compound_scalper.tripped, open_positions[].pnl_pct, trade_log[].ok,
   alert_feed[].noise/band -- nothing new from the backend. */
function renderAttentionAndHighlights(data) {
  const items = []; // {level: 'bad'|'warn'|'ok', text}
  const s = data.compound_scalper || {};
  if (s.started && s.tripped) {
    items.push({ level: "bad", text: `Scalper pool circuit tripped -- ${esc(s.tripped_reason || "no reason given")}.` });
  }

  const recentTrades = (data.trade_log || []).slice(0, 5);
  const failedTrades = recentTrades.filter(t => !t.ok);
  if (failedTrades.length) {
    items.push({ level: "bad", text: `${failedTrades.length} of your last ${recentTrades.length} trade attempt(s) failed to execute -- check Positions &rarr; Trade History.` });
  }

  const pos = data.open_positions || [];
  const bigLosers = pos.filter(p => p.pnl_pct != null && p.pnl_pct <= -20);
  const bigWinners = pos.filter(p => p.pnl_pct != null && p.pnl_pct >= 50);
  if (bigLosers.length) {
    items.push({ level: "warn", text: `${bigLosers.length} open position(s) down 20%+: ${bigLosers.map(p => esc(p.token) + " (" + p.pnl_pct.toFixed(0) + "%)").join(", ")}.` });
  }
  if (bigWinners.length) {
    items.push({ level: "ok", text: `${bigWinners.length} open position(s) up 50%+: ${bigWinners.map(p => esc(p.token) + " (+" + p.pnl_pct.toFixed(0) + "%)").join(", ")}.` });
  }

  const allFeed = data.alert_feed || [];
  const actionable = allFeed.filter(a => !a.noise && (a.band === "A" || a.band === "B"));
  const newActionableCount = actionable.length;
  if (newActionableCount) {
    items.push({ level: "ok", text: `${newActionableCount} band A/B alert(s) in the current feed -- worth a look on the Alerts tab.` });
  }

  let overallLevel = "ok";
  if (items.some(i => i.level === "bad")) overallLevel = "bad";
  else if (items.some(i => i.level === "warn")) overallLevel = "warn";

  const hh = data.health || {};
  const staleRunners = (hh.runners || []).filter(r => r.stale).map(r => r.name);
  if (staleRunners.length) {
    items.push({ level: "warn", text: `Not running on schedule: ${esc(staleRunners.join(", "))} -- see System tab.` });
  }
  if (hh.fomo_backoff) {
    items.push({ level: "warn", text: `Fomo API is out of credits -- roster buys/theses paused until credits reset or are topped up.` });
  }
  const attnEl = document.getElementById("attention");
  if (!items.length) {
    attnEl.innerHTML = `<div class="attn attn-ok"><h3>All clear</h3>Nothing urgent right now -- no tripped circuits, no failed trades, no big moves on open positions.</div>`;
  } else {
    attnEl.innerHTML = `<div class="attn attn-${overallLevel}"><h3>${overallLevel === "bad" ? "Needs attention" : overallLevel === "warn" ? "Worth a look" : "Heads up"}</h3><ul>${items.map(i => `<li>${i.text}</li>`).join("")}</ul></div>`;
  }

  // Overview tab: last 5 non-noise alerts as a compact list instead of a full table.
  const highlights = allFeed.filter(a => !a.noise).slice(0, 5);
  document.getElementById("recent_highlights").innerHTML = highlights.length ? `
    <div class="highlight-list">${highlights.map(a => `
      <div class="highlight-item">
        <div class="hl-left"><span class="${a.band ? 'band-' + esc(a.band) : ''}">${esc(a.band || '-')}</span>
          <span class="cat-${esc(a.category)}">${esc(a.category)}</span>
          <strong>${esc(a.token_symbol)}</strong> ${esc(a.headline)}</div>
        <span style="color:#8a8f98; font-size:11px;" title="${esc(a.ts_fmt)}">${esc(a.ago)}</span>
      </div>`).join("")}</div>` : '<div class="empty">No actionable alerts yet -- check the Alerts tab to see filtered noise if you want the full picture.</div>';

  // Tab badges: how many open positions need a decision, how many new-ish alerts.
  const badgePos = document.getElementById("badge-positions");
  if (bigLosers.length) { badgePos.style.display = "inline-block"; badgePos.textContent = bigLosers.length; }
  else { badgePos.style.display = "none"; }
  const badgeAlerts = document.getElementById("badge-alerts");
  if (newActionableCount) { badgeAlerts.style.display = "inline-block"; badgeAlerts.textContent = newActionableCount; }
  else { badgeAlerts.style.display = "none"; }
}

function render(data) {
  document.getElementById("meta").textContent =
    `Generated ${data.generated_at} · state backend: ${data.state_backend}`;

  const pnl = data.realized_pnl || {};
  const scalperTile = (() => {
    const s = data.compound_scalper || {};
    if (!s.started) return '<span class="na">not started</span>';
    if (!s.enabled) return '<span class="na">disabled</span>';
    const trip = s.tripped ? ' <span class="neg-pnl">(tripped)</span>' : '';
    return '$' + (s.balance_usd || 0).toFixed(2) + trip;
  })();
  document.getElementById("statbar").innerHTML = `
    <div class="stat"><div class="label">Open positions</div><div class="value">${data.open_positions.length}</div></div>
    <div class="stat"><div class="label">Unrealized P&amp;L</div><div class="value">${fmtUsd(data.unrealized_pnl_usd)}</div></div>
    <div class="stat"><div class="label">Realized P&amp;L</div><div class="value">${fmtUsd(pnl.total_realized_pnl_usd || 0)}</div></div>
    <div class="stat"><div class="label">Win / Loss (closed)</div><div class="value"><span class="pos-pnl">${pnl.wins || 0}W</span> / <span class="neg-pnl">${pnl.losses || 0}L</span></div></div>
    <div class="stat"><div class="label">Scalper pool</div><div class="value">${scalperTile}</div></div>
    <div class="stat"><div class="label">Modules ready</div><div class="value">${data.modules_ready_count}/${data.modules_total_count}</div></div>`;

  renderAttentionAndHighlights(data);

  const s = data.compound_scalper || {};
  document.getElementById("scalper-tag").textContent =
    !s.started ? "not started" : (!s.enabled ? "disabled" : (s.tripped ? "tripped" : (s.session_expired ? "session expired" : "running")));
  if (!s.started) {
    document.getElementById("scalper_detail").innerHTML = '<div class="empty">Pool not started -- executor.compound_scalper.init_pool() has not been called yet (deliberate manual step, see the docstring for that module).</div>';
  } else {
    const mult = s.multiple_of_seed != null ? s.multiple_of_seed.toFixed(2) + "x" : "-";
    const op = s.open_position;
    document.getElementById("scalper_detail").innerHTML = `
      <div class="statbar">
        <div class="stat"><div class="label">Balance</div><div class="value">$${(s.balance_usd||0).toFixed(2)}</div></div>
        <div class="stat"><div class="label">Seed</div><div class="value">$${(s.seed_usd||0).toFixed(2)}</div></div>
        <div class="stat"><div class="label">Multiple of seed</div><div class="value">${mult}</div></div>
        <div class="stat"><div class="label">Realized P&amp;L</div><div class="value">${fmtUsd(s.realized_pnl_usd)}</div></div>
        <div class="stat"><div class="label">Trades this session</div><div class="value">${s.trades_this_session||0}</div></div>
        <div class="stat"><div class="label">Consecutive losses</div><div class="value">${s.consecutive_losses||0}</div></div>
      </div>
      ${s.tripped ? `<div class="empty" style="color:#e74c3c">Circuit tripped: ${esc(s.tripped_reason||"")}</div>` : ""}
      ${op ? `<table><thead><tr><th>Token</th><th>Chain</th><th>Position $</th><th>Peak multiple</th><th>Partial TP done</th><th>Opened</th></tr></thead>
        <tbody><tr><td class="mono">${esc(op.token)}</td><td>${esc(op.chain)}</td><td>$${(op.position_usd||0).toFixed(2)}</td>
        <td>${(op.peak_multiple||1).toFixed(2)}x</td><td>${op.partial_tp_done ? "yes" : "no"}</td><td>${esc(op.opened_fmt||"?")}</td></tr></tbody></table>`
        : '<div class="empty">No open scalp position right now.</div>'}
    `;
  }

  const pos = data.open_positions;
  const execBadge = (status) => {
    const cls = { real: "exec-real", failed: "exec-failed", unconfirmed: "exec-unconfirmed", pending: "exec-pending" }[status] || "exec-pending";
    const label = { real: "REAL BUY", failed: "NOT BOUGHT", unconfirmed: "UNCONFIRMED", pending: "PENDING" }[status] || status.toUpperCase();
    return `<span class="${cls}">${label}</span>`;
  };
  document.getElementById("positions").innerHTML = pos.length ? `
    <table><thead><tr><th>Token</th><th>Chain</th><th>Stages</th><th>Staked $</th><th>Execution</th><th>Current value</th><th>P&amp;L $</th><th>P&amp;L %</th><th>Remaining</th><th>Opened</th></tr></thead>
    <tbody>${pos.map(p => `<tr>
      <td class="mono">${esc(p.token)}</td><td>${esc(p.chain)}</td>
      <td>${esc(Object.keys(p.stages || {}).join(", "))}${p.double_confirmed ? ' <span class="tag">2x confirmed</span>' : ""}</td>
      <td>$${(p.total_usd || 0).toFixed(2)}</td>
      <td>${execBadge(p.execution_status)}<div class="exec-detail">${esc(p.execution_detail || "")}</div></td>
      <td>${p.price_unavailable ? '<span class="na">price unavailable</span>' : '$' + (p.value_usd || 0).toFixed(2)}</td>
      <td>${fmtUsd(p.pnl_usd)}</td>
      <td>${fmtPct(p.pnl_pct)}</td>
      <td>${((p.remaining_pct != null ? p.remaining_pct : 1) * 100).toFixed(0)}%</td>
      <td>${esc(p.opened_fmt)}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">No open positions right now.</div>';

  const closed = data.closed_positions || [];
  document.getElementById("closed_positions").innerHTML = closed.length ? `
    <table><thead><tr><th>Token</th><th>Chain</th><th>Closed</th><th>Reason</th><th>Staked $</th><th>Exit $</th><th>P&amp;L</th></tr></thead>
    <tbody>${closed.map(p => `<tr>
      <td class="mono">${esc(p.token)}</td><td>${esc(p.chain)}</td>
      <td>${esc(p.closed_fmt)}</td>
      <td>${esc(p.close_reason)}</td>
      <td>$${(p.total_usd || 0).toFixed(2)}</td>
      <td>${p.exit_usd != null ? '$' + p.exit_usd.toFixed(2) : '<span class="na">unpriced</span>'}</td>
      <td>${fmtUsd(p.pnl_usd)}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">No closed positions yet.</div>';

  const trades = data.trade_log || [];
  document.getElementById("trade_log").innerHTML = trades.length ? `
    <table><thead><tr><th>When</th><th>Side</th><th>Chain</th><th>Token</th><th>USD</th><th>OK</th><th>Reason</th></tr></thead>
    <tbody>${trades.slice(0, 30).map(t => `<tr>
      <td title="${esc(t.ts_fmt)}">${esc(t.ago)}</td>
      <td class="side-${esc(t.side)}">${esc((t.side || "").toUpperCase())}</td>
      <td>${esc(t.chain)}</td><td class="mono">${esc(t.token)}</td>
      <td>${t.usd_amount != null ? '$' + Number(t.usd_amount).toFixed(2) : '-'}</td>
      <td class="${t.ok ? 'ok-yes' : 'ok-no'}">${t.ok ? 'yes' : 'no'}</td>
      <td>${esc(t.reason)}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">No trades logged yet.</div>';

  const fomoSignals = data.fomo_signals || [];
  document.getElementById("fomo_signals").innerHTML = fomoSignals.length ? `
    <table><thead><tr><th>When</th><th>Type</th><th>Trader</th><th>Tier</th><th>Token</th><th>Our Score</th><th>24h PnL</th><th>7d PnL</th><th>30d PnL</th><th>Thesis / Detail</th><th>Links</th></tr></thead>
    <tbody>${fomoSignals.map(f => `<tr>
      <td title="${esc(f.ts_fmt)}">${esc(f.ago)}</td>
      <td class="${f.kind === 'thesis' ? 'cat-thesis' : 'cat-buy'}">${esc((f.kind || '').toUpperCase())}</td>
      <td>${esc(f.trader)}</td>
      <td>${esc(f.tier)}</td>
      <td class="mono">${esc(f.token_symbol)}</td>
      <td class="${f.band ? 'band-' + esc(f.band) : ''}">${f.score != null ? f.score + ' (' + esc(f.band) + ')' : '<span class="na">not scored</span>'}</td>
      <td>${fmtUsd(f.pnl_24h)}</td>
      <td>${fmtUsd(f.pnl_7d)}</td>
      <td>${fmtUsd(f.pnl_30d)}</td>
      <td>${esc(f.thesis_text || f.detail)}${f.thesis_link ? ` <a class="linkbtn" href="${esc(f.thesis_link)}" target="_blank" rel="noopener">link</a>` : ''}</td>
      <td>${Object.entries(f.links || {}).map(([label,url]) => `<a class="linkbtn" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`).join(" ")}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">No Fomo roster buys or theses detected yet this cycle.</div>';

  const fomoCandidates = data.fomo_candidates || [];
  document.getElementById("fomo_candidates").innerHTML = fomoCandidates.length ? `
    <table><thead><tr><th>Seen</th><th>Handle</th><th>Display name</th><th>Balance</th><th>24h PnL</th><th>7d PnL</th><th>30d PnL</th><th>Volume</th><th>Insider?</th><th>Status</th></tr></thead>
    <tbody>${fomoCandidates.map(c => `<tr>
      <td>${esc(c.ago)}</td>
      <td class="mono">${esc(c.handle)}</td>
      <td>${esc(c.display_name)}</td>
      <td>$${Number(c.balance_usd || 0).toLocaleString()}</td>
      <td>${fmtUsd(c.pnl_24h != null ? c.pnl_24h : c.pnl_usd)}</td>
      <td>${fmtUsd(c.pnl_7d)}</td>
      <td>${fmtUsd(c.pnl_30d)}</td>
      <td>${c.volume_usd != null ? '$' + Number(c.volume_usd).toLocaleString() : '-'}</td>
      <td title="${esc(c.insider_detail)}">${c.insider === true ? '<span class="neg-pnl">INSIDER</span>' : c.insider === false ? 'no' : '<span class="na">unknown</span>'}</td>
      <td title="${esc(c.promotion_reason)}">${c.promoted ? '<span class="pos-pnl">auto-promoted</span>' : '<span class="na">watching</span>'}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">No new-trader candidates above $5k found yet.</div>';

  const allFeed = data.alert_feed || [];
  const shown = showNoise ? allFeed : allFeed.filter(a => !a.noise);
  const hiddenCount = allFeed.length - allFeed.filter(a => !a.noise).length;
  document.getElementById("noise-toggle").textContent = showNoise
    ? "Hide band C/D noise"
    : `Show all (incl. ${hiddenCount} band C/D noise)`;
  document.getElementById("noise-toggle").className = "toggle" + (showNoise ? " active" : "");
  document.getElementById("feed").innerHTML = shown.length ? `
    <table><thead><tr><th>When</th><th>Category</th><th>Band</th><th>Token</th><th>Headline</th><th>Auto-buy</th><th>Tags</th><th>Links</th></tr></thead>
    <tbody>${shown.map(a => `<tr>
      <td title="${esc(a.ts_fmt)}">${esc(a.ago)}</td>
      <td class="cat-${esc(a.category)}">${esc(a.category)}</td>
      <td class="${a.band ? 'band-' + esc(a.band) : ''}">${esc(a.band || '-')}</td>
      <td>${esc(a.token_symbol)}</td>
      <td>${esc(a.headline)}</td>
      <td class="${a.autobuy && a.autobuy.level === 'ok' ? 'pos-pnl' : 'na'}">${esc(a.autobuy ? a.autobuy.text : '-')}</td>
      <td>${Object.entries(a.tags || {}).map(([k,v]) => `<span class="tag">${esc(k)}: ${esc(v)}</span>`).join("")}</td>
      <td>${Object.entries(a.links || {}).map(([label,url]) => `<a class="linkbtn" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`).join(" ")}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">No alerts to show (try the toggle above if you want to see filtered noise too).</div>';
  document.getElementById("noise-toggle").onclick = () => { showNoise = !showNoise; render(window.__lastData); };

  const pp = data.paper || {};
  const ov = pp.overall || {};
  const tgt = data.win_rate_target || 0.8;
  const pctCell = (st) => st && st.n ? `<span class="${st.win_rate >= tgt ? 'pos-pnl' : 'neg-pnl'}">${(st.win_rate * 100).toFixed(0)}%</span>` : '<span class="na">-</span>';
  const groupTable = (title, grp) => {
    const rows = Object.entries(grp || {});
    if (!rows.length) return '';
    return `<h4 style="margin:12px 0 4px">${esc(title)}</h4><table><thead><tr><th></th><th>Trades</th><th>Win rate</th><th>Avg win</th><th>Avg loss</th><th>Expectancy</th><th>P&amp;L</th></tr></thead>
      <tbody>${rows.map(([k, st]) => `<tr><td>${esc(k)}</td><td>${st.n}</td><td>${pctCell(st)}</td>
        <td>${st.avg_win_pct != null ? '+' + st.avg_win_pct + '%' : '-'}</td><td>${st.avg_loss_pct != null ? st.avg_loss_pct + '%' : '-'}</td>
        <td>${st.expectancy_pct != null ? st.expectancy_pct + '%' : '-'}</td><td>${fmtUsd(st.total_pnl_usd)}</td></tr>`).join("")}</tbody></table>`;
  };
  document.getElementById("paper").innerHTML = ov.n ? `
    <p><strong>${ov.n}</strong> closed paper trades &middot; win rate ${pctCell(ov)} (target ${(tgt * 100).toFixed(0)}%)
      &middot; expectancy ${ov.expectancy_pct}% per trade &middot; total ${fmtUsd(ov.total_pnl_usd)} &middot; ${pp.open_count || 0} open</p>
    ${groupTable("By signal", pp.by_signal)}${groupTable("By chain", pp.by_chain)}${groupTable("By exit", pp.by_exit_type)}`
    : `<div class="empty">No closed paper trades yet -- ${pp.open_count || 0} open. Every buy signal is paper-traded automatically; results appear here as they close.</div>`;

  const h = data.health || {};
  document.getElementById("health").innerHTML = `
    <table><thead><tr><th>Runner</th><th>Last cycle</th><th>Where</th><th>Note</th></tr></thead>
    <tbody>${(h.runners || []).map(r => `<tr>
      <td>${esc(r.name)}</td>
      <td class="${r.stale ? 'neg-pnl' : 'pos-pnl'}">${esc(r.ago)}${r.stale ? ' (not running on schedule)' : ''}</td>
      <td>${esc(r.where)}</td><td>${esc(r.note)}</td></tr>`).join("")}</tbody></table>
    <p>Fomo API credits remaining: <strong>${h.fomo_credits_remaining == null ? 'unknown' : Number(h.fomo_credits_remaining).toLocaleString()}</strong>
      &middot; spent today: ${Number(h.fomo_spent_today || 0).toLocaleString()}
      ${h.fomo_backoff ? ' &middot; <span class="neg-pnl">OUT OF CREDITS -- paused, retrying every 6h</span>' : ''}</p>
    <p>MadeOnSol calls today: <strong>${h.madeonsol ? h.madeonsol.used : '?'}</strong> of ${h.madeonsol ? h.madeonsol.budget : '?'}
      (paced across the day -- routine scans allowed so far: ${h.madeonsol ? h.madeonsol.routine_allowance_now : '?'}, resets 5:00 AM PKT)</p>
    <p>Auto-promoted traders: ${(h.fomo_promoted || []).map(p => esc(p.display_name)).join(", ") || 'none yet'}</p>
    <p>Most active Fomo traders NOT on your roster: ${(h.fomo_unmatched || []).map(u => esc(u.handle) + ' (' + u.count + ')').join(", ") || 'none recorded yet'}</p>`;

  const mods = data.modules || [];
  document.getElementById("modules-summary").innerHTML = `
    <span class="dot" style="background:${data.modules_ready_count === data.modules_total_count ? '#2ecc71' : '#f1c40f'}"></span>
    <strong>${data.modules_ready_count}/${data.modules_total_count} modules ready</strong>
    <span style="color:#8a8f98;">(click to ${modulesOpen ? "collapse" : "expand"})</span>`;
  document.getElementById("modules-summary").onclick = () => { modulesOpen = !modulesOpen; render(window.__lastData); };
  const detailEl = document.getElementById("modules-detail");
  detailEl.className = "modules-detail" + (modulesOpen ? " open" : "");
  detailEl.innerHTML = mods.map(m => `
    <div class="mcard"><h4><span class="mdot ${m.ready ? 'ready' : 'blocked'}"></span>${esc(m.name)}</h4>
      <div class="note">${esc(m.note)}</div></div>`).join("");
}

let __seenAlertKeys = null;
let __seenTradeKeys = null;
let __notifyReady = false;

function __alertKey(a) { return (a.layer||"") + "|" + (a.token_address||"") + "|" + (a.ts||""); }
function __tradeKey(t) { return (t.tx_signature||"") + "|" + (t.token||"") + "|" + (t.ts||""); }

function __beep(freq) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = freq || 880;
    osc.connect(gain); gain.connect(ctx.destination);
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    osc.start();
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
    osc.stop(ctx.currentTime + 0.5);
  } catch (e) {}
}

function __notify(title, body) {
  __beep(880);
  if (__notifyReady && "Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body: body }); } catch (e) {}
  }
  document.title = "\U0001F534 " + title;
  setTimeout(() => { document.title = "S1c Dashboard"; }, 8000);
}

function __checkNewAlertsAndTrades(data) {
  const alertKeys = new Set((data.alert_feed || []).map(__alertKey));
  const tradeKeys = new Set((data.trade_log || []).map(__tradeKey));

  if (__seenAlertKeys === null) {
    __seenAlertKeys = alertKeys;
  } else {
    for (const a of (data.alert_feed || [])) {
      const k = __alertKey(a);
      if (!__seenAlertKeys.has(k) && !a.noise) {
        __notify("New alert: " + (a.token_symbol || a.headline || "gem alert"), a.headline || "");
      }
    }
    __seenAlertKeys = alertKeys;
  }

  if (__seenTradeKeys === null) {
    __seenTradeKeys = tradeKeys;
  } else {
    for (const t of (data.trade_log || [])) {
      const k = __tradeKey(t);
      if (!__seenTradeKeys.has(k)) {
        const label = t.ok ? "AUTO-BUY/SELL EXECUTED" : "TRADE ATTEMPT FAILED";
        __notify(label + ": " + (t.side || "").toUpperCase() + " " + (t.token || ""),
                  "$" + (t.usd_amount != null ? Number(t.usd_amount).toFixed(2) : "?") + " -- " + (t.reason || ""));
      }
    }
    __seenTradeKeys = tradeKeys;
  }
}

async function poll() {
  try {
    const res = await fetch("/api/data");
    const data = await res.json();
    window.__lastData = data;
    __checkNewAlertsAndTrades(data);
    render(data);
  } catch (e) {
    document.getElementById("meta").textContent = "fetch failed: " + e;
  }
}
if ("Notification" in window && Notification.permission !== "granted" && Notification.permission !== "denied") {
  Notification.requestPermission().then(() => { __notifyReady = true; });
} else if ("Notification" in window && Notification.permission === "granted") {
  __notifyReady = true;
}
poll();
setInterval(poll, 10000);
</script>
</body></html>
"""


# Sept 30 2026 -- Ali's real dashboard log showed several /api/data
# requests overlapping in flight at once (the frontend polls every 10s,
# but one full build_data() call was taking 12-26s on his real network --
# see utils/http.py's connection-pooling fix for the main cause -- so a
# new poll fired before the previous one finished, stacking redundant
# concurrent Upstash/DexScreener calls). This short-TTL cache means a
# request that arrives while a very recent one is still fresh gets served
# that result instantly instead of triggering a whole new, expensive
# rebuild -- purely a performance/cost guard, never a staleness risk the
# person would notice: 8s is under the 10s poll interval, so the page
# still gets new data every real poll cycle.
_last_data_cache = {"data": None, "built_at": 0.0}
_last_data_lock = threading.Lock()
_DATA_CACHE_TTL_SECONDS = 8.0


def _build_data_cached() -> dict:
    """Holds _last_data_lock across the ENTIRE build (not just the cache
    read/write) so two requests that arrive close together -- the overlap
    Ali's log showed -- never trigger two concurrent, redundant
    build_data() calls: the second one simply waits for the first to
    finish and then gets served its fresh result from cache instead of
    starting its own."""
    with _last_data_lock:
        now = time.time()
        if _last_data_cache["data"] is not None and (now - _last_data_cache["built_at"]) < _DATA_CACHE_TTL_SECONDS:
            return _last_data_cache["data"]
        data = build_data()
        _last_data_cache["data"] = data
        _last_data_cache["built_at"] = time.time()
        return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the console quiet; errors still show via do_GET's own prints

    def do_GET(self):
        if self.path.startswith("/api/data"):
            try:
                payload = json.dumps(_build_data_cached()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as e:
                print(f"[dashboard] /api/data error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
        body = PAGE_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"S1c dashboard running at http://localhost:{PORT} (state backend: "
          f"{state.backend() if hasattr(state, 'backend') else '?'}) -- Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
