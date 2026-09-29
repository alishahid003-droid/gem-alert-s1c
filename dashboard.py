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
import time
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


def build_data() -> dict:
    report = readiness_report()
    feed = state.get_alert_feed(limit=150)
    for item in feed:
        item["ago"] = _ago(item["ts"])
        item["ts_fmt"] = _fmt_ts(item["ts"])
        item["category"] = LAYER_CATEGORY.get(item.get("layer"), item.get("layer") or "other")
        item["band"] = _extract_band(item.get("tags"))
        item["noise"] = item["category"] in BAND_FILTERABLE_CATEGORIES and item["band"] in ("C", "D")
        item["links"] = links.build_links(item.get("chain"), item.get("token_address"))

    positions = [_with_live_pnl(p) for p in position_state.list_open_positions()]
    for p in positions:
        p["opened_fmt"] = _fmt_ts(p.get("opened_ts")) if p.get("opened_ts") else "?"

    closed_positions = position_state.list_closed_positions(limit=100)
    for p in closed_positions:
        p["opened_fmt"] = _fmt_ts(p.get("opened_ts")) if p.get("opened_ts") else "?"
        p["closed_fmt"] = _fmt_ts(p.get("closed_ts")) if p.get("closed_ts") else "?"

    trade_log = state.get_trade_log(limit=200)
    for t in trade_log:
        t["ago"] = _ago(t["ts"])
        t["ts_fmt"] = _fmt_ts(t["ts"])
        t["explorer"] = links.build_links(t.get("chain"), t.get("token")) if t.get("tx_signature") else {}

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

    return {
        "generated_at": _fmt_ts(time.time()),
        "readiness": report,
        "modules_ready_count": sum(1 for m in modules if m["ready"]),
        "modules_total_count": len(modules),
        "modules": modules,
        "alert_feed": feed,
        "open_positions": positions,
        "closed_positions": closed_positions,
        "trade_log": trade_log,
        "realized_pnl": position_state.realized_pnl_summary(),
        "unrealized_pnl_usd": unrealized_total,
        "state_backend": state.backend() if hasattr(state, "backend") else "?",
        "compound_scalper": scalper_status,
    }


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
</style></head>
<body>
<h1>S1c &mdash; Fomo Gem-Alert System</h1>
<div class="sub" id="meta">loading...</div>

<div class="statbar" id="statbar"></div>

<section>
  <h2>Compound Scalper <span class="tag" id="scalper-tag"></span></h2>
  <div id="scalper_detail"></div>
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

<section>
  <h2>
    Alerts
    <button class="toggle" id="noise-toggle">Show all (incl. band C/D noise)</button>
  </h2>
  <div id="feed"></div>
</section>

<section>
  <h2>Modules</h2>
  <div class="modules-summary" id="modules-summary"></div>
  <div class="modules-detail" id="modules-detail"></div>
</section>

<script>
function esc(s) { return (s === undefined || s === null) ? "" : String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function fmtUsd(v) { return v == null ? '<span class="na">unpriced</span>' : (v >= 0 ? '<span class="pos-pnl">$' : '<span class="neg-pnl">-$') + Math.abs(v).toFixed(2) + '</span>'; }
function fmtPct(v) { return v == null ? '<span class="na">-</span>' : (v >= 0 ? '<span class="pos-pnl">+' : '<span class="neg-pnl">') + v.toFixed(1) + '%</span>'; }

let showNoise = false;
let modulesOpen = false;

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

  const s = data.compound_scalper || {};
  document.getElementById("scalper-tag").textContent =
    !s.started ? "not started" : (!s.enabled ? "disabled" : (s.tripped ? "tripped" : (s.session_expired ? "session expired" : "running")));
  if (!s.started) {
    document.getElementById("scalper_detail").innerHTML = '<div class="empty">Pool not started -- executor.compound_scalper.init_pool() hasn\'t been called yet (deliberate manual step, see that module\'s docstring).</div>';
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
  document.getElementById("positions").innerHTML = pos.length ? `
    <table><thead><tr><th>Token</th><th>Chain</th><th>Stages</th><th>Staked $</th><th>Current value</th><th>P&amp;L $</th><th>P&amp;L %</th><th>Remaining</th><th>Opened</th></tr></thead>
    <tbody>${pos.map(p => `<tr>
      <td class="mono">${esc(p.token)}</td><td>${esc(p.chain)}</td>
      <td>${esc(Object.keys(p.stages || {}).join(", "))}${p.double_confirmed ? ' <span class="tag">2x confirmed</span>' : ""}</td>
      <td>$${(p.total_usd || 0).toFixed(2)}</td>
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

  const allFeed = data.alert_feed || [];
  const shown = showNoise ? allFeed : allFeed.filter(a => !a.noise);
  const hiddenCount = allFeed.length - allFeed.filter(a => !a.noise).length;
  document.getElementById("noise-toggle").textContent = showNoise
    ? "Hide band C/D noise"
    : `Show all (incl. ${hiddenCount} band C/D noise)`;
  document.getElementById("noise-toggle").className = "toggle" + (showNoise ? " active" : "");
  document.getElementById("feed").innerHTML = shown.length ? `
    <table><thead><tr><th>When</th><th>Category</th><th>Band</th><th>Token</th><th>Headline</th><th>Tags</th><th>Links</th></tr></thead>
    <tbody>${shown.map(a => `<tr>
      <td title="${esc(a.ts_fmt)}">${esc(a.ago)}</td>
      <td class="cat-${esc(a.category)}">${esc(a.category)}</td>
      <td class="${a.band ? 'band-' + esc(a.band) : ''}">${esc(a.band || '-')}</td>
      <td>${esc(a.token_symbol)}</td>
      <td>${esc(a.headline)}</td>
      <td>${Object.entries(a.tags || {}).map(([k,v]) => `<span class="tag">${esc(k)}: ${esc(v)}</span>`).join("")}</td>
      <td>${Object.entries(a.links || {}).map(([label,url]) => `<a class="linkbtn" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`).join(" ")}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">No alerts to show (try the toggle above if you want to see filtered noise too).</div>';
  document.getElementById("noise-toggle").onclick = () => { showNoise = !showNoise; render(window.__lastData); };

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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the console quiet; errors still show via do_GET's own prints

    def do_GET(self):
        if self.path.startswith("/api/data"):
            try:
                payload = json.dumps(build_data()).encode("utf-8")
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
