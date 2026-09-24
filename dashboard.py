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

NO PRIOR DASHBOARD FOUND to build on -- checked memory (/areas/s1c-fomo.md)
for an earlier design/screenshot Ali mentioned; that file has no dashboard
reference, and an earlier repo-wide search this session (find/grep across
the whole project) found no HTML/frontend files anywhere before this one.
Built fresh.
"""
import json
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import state
import links
from scheduler import readiness_report
import executor.position_state as position_state

PORT = 8787


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
}


def build_data() -> dict:
    report = readiness_report()
    feed = state.get_alert_feed(limit=150)
    for item in feed:
        item["ago"] = _ago(item["ts"])
        item["ts_fmt"] = _fmt_ts(item["ts"])
        item["category"] = LAYER_CATEGORY.get(item.get("layer"), item.get("layer") or "other")
        item["links"] = links.build_links(item.get("chain"), item.get("token_address"))
    positions = position_state.list_open_positions()
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

    return {
        "generated_at": _fmt_ts(time.time()),
        "readiness": report,
        "alert_feed": feed,
        "open_positions": positions,
        "closed_positions": closed_positions,
        "trade_log": trade_log,
        "realized_pnl": position_state.realized_pnl_summary(),
        "state_backend": state.backend() if hasattr(state, "backend") else "?",
    }


PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>S1c Dashboard</title>
<style>
  :root { color-scheme: dark; }
  body { background:#0b0e11; color:#e6e6e6; font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0; padding:0 20px 40px; }
  h1 { font-size:20px; margin:20px 0 4px; }
  .sub { color:#8a8f98; font-size:12px; margin-bottom:20px; }
  .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(260px,1fr)); gap:12px; margin-bottom:28px; }
  .card { background:#161a1f; border:1px solid #2a2f37; border-radius:8px; padding:12px 14px; }
  .card h3 { margin:0 0 6px; font-size:13px; display:flex; justify-content:space-between; align-items:center; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
  .ready { background:#2ecc71; } .blocked { background:#e74c3c; }
  .note { color:#8a8f98; font-size:11px; line-height:1.4; }
  section { margin-bottom:32px; }
  section > h2 { font-size:15px; border-bottom:1px solid #2a2f37; padding-bottom:6px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #21252b; vertical-align:top; }
  th { color:#8a8f98; font-weight:500; }
  .tag { display:inline-block; background:#21252b; border-radius:4px; padding:1px 6px; margin:1px 2px 1px 0; font-size:11px; }
  .cat-gem { color:#2ecc71; } .cat-developer { color:#f1c40f; } .cat-copy-trading { color:#3498db; }
  .cat-sell\\/rug-watch { color:#e74c3c; } .cat-news { color:#9b59b6; } .cat-correlation { color:#e67e22; }
  .cat-backing { color:#1abc9c; } .cat-buzz { color:#ff6ec7; } .cat-insider-watch { color:#ff4757; font-weight:600; }
  .empty { color:#555; font-style:italic; padding:8px; }
  .filters { margin-bottom:10px; }
  .filters button { background:#161a1f; border:1px solid #2a2f37; color:#e6e6e6; border-radius:5px; padding:4px 10px; margin-right:6px; font-size:11px; cursor:pointer; }
  .filters button.active { background:#2a2f37; }
  .pill { background:#21252b; border-radius:4px; padding:2px 8px; font-size:11px; }
  .linkbtn { display:inline-block; background:#1f3a2e; color:#2ecc71; border:1px solid #2a5c40; border-radius:4px; padding:2px 7px; margin:1px 3px 1px 0; font-size:11px; text-decoration:none; }
  .linkbtn:hover { background:#2a5c40; }
  .pos-pnl { color:#2ecc71; } .neg-pnl { color:#e74c3c; }
  .side-buy { color:#2ecc71; font-weight:600; } .side-sell { color:#e67e22; font-weight:600; }
  .ok-yes { color:#2ecc71; } .ok-no { color:#e74c3c; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:11px; }
</style></head>
<body>
<h1>S1c &mdash; Fomo Gem-Alert System</h1>
<div class="sub" id="meta">loading...</div>

<section>
  <h2>Modules</h2>
  <div class="grid" id="modules"></div>
</section>

<section>
  <h2>Open Positions</h2>
  <div id="positions"></div>
</section>

<section>
  <h2>Realized P&amp;L</h2>
  <div class="grid" id="pnl"></div>
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
  <h2>Alert Feed</h2>
  <div class="filters" id="filters"></div>
  <div id="feed"></div>
</section>

<script>
function esc(s) { return (s === undefined || s === null) ? "" : String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

let activeFilter = "all";

function flattenReadiness(r) {
  const rows = [];
  function walk(obj, prefix) {
    for (const [k, v] of Object.entries(obj)) {
      if (v && typeof v === "object" && !Array.isArray(v)) {
        if ("ready" in v) {
          rows.push({name: k, ready: !!v.ready, note: v.note || ""});
        } else {
          walk(v, prefix + k + ".");
        }
      } else if (typeof v === "boolean") {
        rows.push({name: k, ready: v, note: ""});
      }
    }
  }
  walk(r, "");
  return rows;
}

function render(data) {
  document.getElementById("meta").textContent =
    `Generated ${data.generated_at} · state backend: ${data.state_backend} · ${data.open_positions.length} open position(s) · ${data.alert_feed.length} recent alert(s)`;

  const pnl = data.realized_pnl || {};
  const pnlVal = pnl.total_realized_pnl_usd || 0;
  document.getElementById("pnl").innerHTML = `
    <div class="card"><h3>Total realized P&amp;L</h3>
      <div class="${pnlVal >= 0 ? 'pos-pnl' : 'neg-pnl'}" style="font-size:20px;">$${pnlVal.toFixed(2)}</div>
      <div class="note">${pnl.priced_count || 0} priced / ${pnl.closed_count || 0} closed positions</div></div>
    <div class="card"><h3>Win / Loss</h3>
      <div style="font-size:20px;"><span class="pos-pnl">${pnl.wins || 0}W</span> / <span class="neg-pnl">${pnl.losses || 0}L</span></div>
      <div class="note">closed positions with a priced exit</div></div>`;

  const modules = flattenReadiness(data.readiness);
  document.getElementById("modules").innerHTML = modules.map(m => `
    <div class="card">
      <h3><span><span class="dot ${m.ready ? 'ready' : 'blocked'}"></span>${esc(m.name)}</span>
      <span class="pill">${m.ready ? 'READY' : 'BLOCKED'}</span></h3>
      <div class="note">${esc(m.note)}</div>
    </div>`).join("") || '<div class="empty">no module data</div>';

  const pos = data.open_positions;
  document.getElementById("positions").innerHTML = pos.length ? `
    <table><thead><tr><th>Chain</th><th>Token</th><th>Stages</th><th>Total $</th><th>Opened</th><th>Double-confirmed</th></tr></thead>
    <tbody>${pos.map(p => `<tr>
      <td>${esc(p.chain)}</td><td>${esc(p.token)}</td>
      <td>${esc(Object.keys(p.stages || {}).join(", "))}</td>
      <td>$${(p.total_usd || 0).toFixed(2)}</td>
      <td>${esc(p.opened_fmt)}</td>
      <td>${p.double_confirmed ? "yes" : "no"}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">no open positions</div>';

  const closed = data.closed_positions || [];
  document.getElementById("closed_positions").innerHTML = closed.length ? `
    <table><thead><tr><th>Chain</th><th>Token</th><th>Opened</th><th>Closed</th><th>Reason</th><th>Entry $</th><th>Exit $</th><th>P&amp;L</th></tr></thead>
    <tbody>${closed.map(p => `<tr>
      <td>${esc(p.chain)}</td><td class="mono">${esc(p.token)}</td>
      <td>${esc(p.opened_fmt)}</td><td>${esc(p.closed_fmt)}</td>
      <td>${esc(p.close_reason)}</td>
      <td>$${(p.total_usd || 0).toFixed(2)}</td>
      <td>${p.exit_usd != null ? '$' + p.exit_usd.toFixed(2) : '<span class="note">unpriced</span>'}</td>
      <td class="${(p.pnl_usd || 0) >= 0 ? 'pos-pnl' : 'neg-pnl'}">${p.pnl_usd != null ? '$' + p.pnl_usd.toFixed(2) : '-'}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">no closed positions yet</div>';

  const trades = data.trade_log || [];
  document.getElementById("trade_log").innerHTML = trades.length ? `
    <table><thead><tr><th>When</th><th>Side</th><th>Chain</th><th>Token</th><th>Amount</th><th>USD</th><th>OK</th><th>Reason</th><th>Tx</th></tr></thead>
    <tbody>${trades.map(t => `<tr>
      <td title="${esc(t.ts_fmt)}">${esc(t.ago)}</td>
      <td class="side-${esc(t.side)}">${esc((t.side || "").toUpperCase())}</td>
      <td>${esc(t.chain)}</td><td class="mono">${esc(t.token)}</td>
      <td>${t.amount_tokens != null ? Number(t.amount_tokens).toLocaleString(undefined, {maximumFractionDigits: 4}) : '-'}</td>
      <td>${t.usd_amount != null ? '$' + Number(t.usd_amount).toFixed(2) : '-'}</td>
      <td class="${t.ok ? 'ok-yes' : 'ok-no'}">${t.ok ? 'yes' : 'no'}</td>
      <td>${esc(t.reason)}</td>
      <td>${t.tx_signature ? `<span class="mono" title="${esc(t.tx_signature)}">${esc(t.tx_signature.slice(0, 10))}&hellip;</span>` : '-'}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">no trades logged yet</div>';

  const cats = ["all", ...new Set(data.alert_feed.map(a => a.category))];
  document.getElementById("filters").innerHTML = cats.map(c =>
    `<button data-cat="${esc(c)}" class="${c === activeFilter ? 'active' : ''}">${esc(c)}</button>`).join("");
  document.querySelectorAll("#filters button").forEach(b => {
    b.onclick = () => { activeFilter = b.dataset.cat; render(window.__lastData); };
  });

  const shown = activeFilter === "all" ? data.alert_feed : data.alert_feed.filter(a => a.category === activeFilter);
  document.getElementById("feed").innerHTML = shown.length ? `
    <table><thead><tr><th>When</th><th>Category</th><th>Layer</th><th>Chain</th><th>Token</th><th>Headline</th><th>Tags</th><th>Buy / Chart</th></tr></thead>
    <tbody>${shown.map(a => `<tr>
      <td title="${esc(a.ts_fmt)}">${esc(a.ago)}</td>
      <td class="cat-${esc(a.category)}">${esc(a.category)}</td>
      <td>${esc(a.layer)}</td><td>${esc(a.chain)}</td>
      <td>${esc(a.token_symbol)}</td>
      <td>${esc(a.headline)}</td>
      <td>${Object.entries(a.tags || {}).map(([k,v]) => `<span class="tag">${esc(k)}: ${esc(v)}</span>`).join("")}</td>
      <td>${Object.entries(a.links || {}).map(([label,url]) => `<a class="linkbtn" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>`).join(" ")}</td>
    </tr>`).join("")}</tbody></table>` : '<div class="empty">no alerts yet</div>';
}

async function poll() {
  try {
    const res = await fetch("/api/data");
    const data = await res.json();
    window.__lastData = data;
    render(data);
  } catch (e) {
    document.getElementById("meta").textContent = "fetch failed: " + e;
  }
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
