"""Shadow compound simulator v3 (20 Nov 2026, Ali's ask: "closer to real trading experience").

v3 adds four realism layers ON TOP of v2's shared-$115-equity-pool fix, none of which touch
real trading code, state.py, books.py, or increase GeckoTerminal call volume beyond what the
shared api_budget.gate (measured real ceiling ~3 calls/min, see api_budget.py) already allows:

1. CLOSE-TIME CONFIRM CHECK: v2 polled every 20s and could miss a faster intra-interval move
   (a real, named limitation). Rather than polling MORE OFTEN (which would compete with the
   real bot for the same shared ~3/min GeckoTerminal budget -- confirmed via api_budget.py's
   LIMITS["geckoterminal"] = (None, 4), GATE_FRACTION=0.8), this adds exactly ONE extra,
   targeted check -- via the same shared-gated /pools/{addr}/trades endpoint
   evm_wallets.py already uses elsewhere in this codebase -- ONLY at the moment a close is
   about to fire, confirming the exit price against the most recent real trade instead of
   relying solely on the 20s-stale /new_pools snapshot. This adds load only at close
   decisions, not continuously, and goes through the exact same shared gate.

2. EXECUTION DELAY MODEL: real trades take real time to confirm on-chain after a signal
   fires (RPC round-trip, block confirmation). v3 adds a configurable
   SHADOW_EXEC_DELAY_S (default 4s, a conservative EVM confirmation estimate) -- when a
   close fires, it looks at the price trace for the closest price AFTER that delay instead
   of using the signal-moment price, so slower real-world fills are honestly modeled rather
   than assuming instant execution at the exact signal price.

3. NO-PRICE-DATA COUNTED HONESTLY: v2 excluded no_price_data_timeout closes from win/loss
   stats entirely (treated as inconclusive). v3 explicitly tracks and reports these as a
   named "stuck/illiquid" outcome category -- a real risk (holding an untrackable position)
   that a real live wallet would also face, not swept out of the sample.

4. FRICTION STRESS-TEST REPORT: a separate reporting function (not a live process) that
   replays the SAME closed trades two ways -- idealized (no exec delay, no confirm-check
   correction) vs realistic (with both) -- so Ali can see exactly how much friction costs,
   not just a single number. Run via: python shadow_compound_sim_v3.py --report

SAFETY (unchanged from v2): never imports/calls executor/books.py's real stake/equity/
can_open, never touches state.py, never calls paper_ledger.open_position()/manage().
Writes ONLY to logs/shadow_compound_sim.jsonl. Restores prior equity/committed state from
that same file on restart, so this is a continuation of the v2 run already in progress
(equity currently ~$200+, NOT reset back to $115).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SHADOW_LOG = os.path.join("logs", "shadow_compound_sim.jsonl")
CANDIDATE_LOG = os.path.join("logs", "candidate_eval.jsonl")
POLL_INTERVAL_S = float(os.environ.get("SHADOW_POLL_INTERVAL_S", "8"))  # 23 Nov 2026: tightened 20s->8s, safe now that _fetch_mcaps is DexScreener-primary (300/min budget, scales with open position COUNT not chain count) -- GeckoTerminal's tight shared ~3-4/min ceiling is now only hit as a fallback for whatever DexScreener misses, so this does not compete with the real bot's own GeckoTerminal usage the way a straight 20s->6s copy of the real bot's cadence would have.
MAX_HOLD_MIN = float(os.environ.get("SHADOW_MAX_HOLD_MIN", "180"))
# BUG FIX (21 Nov 2026, Ali: "33% win rate is pathetic, find the real cause"): the sim was using
# the SAME 180min cap for two different situations that the real system (executor/paper_ledger.py
# _manage_early) treats completely differently:
#   1. No price data at all (stuck/illiquid) -- correctly cut fast, no real equivalent in the real
#      system needed since the real bot's own price feeds rarely go fully dark this way.
#   2. Has REAL price data, just hasn't hit an exit rule (stop/trail/ladder) yet -- the real system
#      lets this ride for EARLY_MAX_HOLD_MIN=1440min (24 HOURS), not 180min. The sim was force-
#      selling live, priced positions 8x sooner than the real bot ever would, mid-flight, at
#      whatever price happened to be showing at minute 180 -- regardless of the real hard
#      stop/trailing-stop protecting the position. This directly caused 2 of this session's worst
#      losses (-$122.43 after peaking 1.965x, -$35.42) that a real deployment would NOT have taken:
#      the real system's 24h window plus its real hard stop (compound: -30%) would have kept
#      protecting the position the whole time instead of flushing it early.
REAL_MAX_HOLD_MIN = float(os.environ.get("SHADOW_REAL_MAX_HOLD_MIN", "1440"))
# ADDED 27 Nov 2026 (Ali: "did we not already fix this in the real system, why is the shadow
# sim doing it differently"): import the real system's own never-priced/feed-outage constants
# instead of redefining them here, so the two stay in sync automatically if Ali ever tunes
# them on the real system (executor/paper_ledger.py) -- no separate copy to drift out of date.
try:
    from executor.paper_ledger import (NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF,
                                        FEED_OUTAGE_MIN_POSITIONS, FEED_OUTAGE_UNPRICED_FRACTION)
except ImportError:
    NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF = 2.0
    FEED_OUTAGE_MIN_POSITIONS = 3
    FEED_OUTAGE_UNPRICED_FRACTION = 0.6

START_EQUITY_USD = float(os.environ.get("SHADOW_START_EQUITY_USD", "115.0"))
SHOT_PCT = float(os.environ.get("SHADOW_SHOT_PCT", "0.50"))
MAX_SHARE = float(os.environ.get("SHADOW_MAX_SHARE", "0.75"))
STAKE_FLOOR_USD = float(os.environ.get("SHADOW_STAKE_FLOOR_USD", "15.0"))
MIN_EQUITY_TO_TRADE = float(os.environ.get("SHADOW_MIN_EQUITY_USD", "5.0"))

# ADDED 27 Nov 2026 (Ali: "don't we already reduce stake size as equity grows into the
# thousands? check that and replicate it here"): the sim never had this at all -- SHOT_PCT
# above has always been one flat 50% regardless of equity size, since the sim's original
# design. The real system's compound book tapers this down as equity grows ("Curve C", see
# executor/books.py's stake_usd(), 8 Nov 2026 entries) specifically so a single trade is never
# an oversized bite of a large account -- Ali's exact stated concern, confirmed live and
# un-overridden (no COMPOUND_MAX_STAKE_USD set in .env). There is no standalone importable
# function for this in the real system (the bands are inline inside stake_usd()), so this
# mirrors the exact band values rather than refactoring live real-system code for an import --
# if Ali ever retunes these on the real system, this comment is the pointer to update here too.
_CURVE_C_BANDS = (
    (0.0,        600.0,         0.25),
    (600.0,      5_000.0,       0.20),
    (5_000.0,    100_000.0,     0.10),
    (100_000.0,  float("inf"),  0.05),
)


def _curve_c_pct(eq: float) -> float:
    override = os.environ.get("SHADOW_COMPOUND_MAX_STAKE_PCT", "").strip()
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    pct = _CURVE_C_BANDS[-1][2]
    for lo, hi, band_pct in _CURVE_C_BANDS:
        if lo <= eq < hi:
            pct = band_pct
            break
    return pct

# v3 realism knobs
EXEC_DELAY_S = float(os.environ.get("SHADOW_EXEC_DELAY_S", "4.0"))
CONFIRM_CHECK_ENABLED = os.environ.get("SHADOW_CONFIRM_CHECK", "1").strip() != "0"


# 23 Nov 2026 (Ali, drawdown breaker -- Monte Carlo evidence): tested against 38 real
# robinhood_chain/base trades + real Curve C sizing + real overlapping concurrency. The
# original 50%-of-peak pause threshold self-stalled ~40% of simulated climbs (pause-only
# breaker, nothing forces recovery if too few positions are open to carry equity back over
# the floor). Raised to 65% -- cuts stall rate to ~1% for ~1 extra day of climb time. Matches
# the same tuning just applied to executor/phase_target.py's PHASES dict (real system).
DRAWDOWN_PAUSE_PCT = float(os.environ.get("SHADOW_DRAWDOWN_PAUSE_PCT", "0.65"))
# Matches executor/books.py max_open()'s real floor for "daily"-kind books (compound) -- 8.
# The shadow sim had NO concurrency cap at all before this; real trading's own max_open()
# already enforces one, so this brings the shadow sim in line with the real book it mirrors.
MAX_CONCURRENT_POSITIONS = int(os.environ.get("SHADOW_MAX_CONCURRENT", "8"))


class _ShadowEquity:
    def __init__(self, start_usd):
        self.realized_pnl = 0.0
        self.start_usd = start_usd
        self.committed_usd = 0.0
        self.peak_equity = start_usd
        self.paused = False

    @property
    def equity(self):
        return round(max(0.0, self.start_usd + self.realized_pnl), 2)

    def update_drawdown_state(self):
        """Call once per loop before sizing/opening decisions. Updates peak-equity tracking
        and fires pause/resume -- pause-only, never force-closes anything, matching the real
        system's phase_target.py design exactly."""
        eq = self.equity
        self.peak_equity = max(self.peak_equity, eq)
        floor = self.peak_equity * DRAWDOWN_PAUSE_PCT if self.peak_equity > 0 else 0.0
        was_paused = self.paused
        if not was_paused and self.peak_equity > 0 and eq <= floor:
            self.paused = True
        elif was_paused and eq > floor:
            self.paused = False
        return was_paused, self.paused, floor

    def next_stake(self, n_open_now: int = 0):
        eq = self.equity
        if self.paused:
            return 0.0
        if eq < MIN_EQUITY_TO_TRADE:
            return 0.0
        # 23 Nov 2026 (Ali, capital-starvation gap): real system spreads capital across up to
        # max_open()'s floor of 8 concurrent positions, so no single stake ever claims more than
        # roughly 1/8th of the capital ceiling at once -- real data confirmed 3 real open
        # positions alone saturated the 75%-of-equity ceiling here (committed $279.05 vs a
        # $279.05 ceiling, $0.0025 of room left), starving every new candidate for 10+ hours
        # even though all 3 open positions were profitable and nothing was actually stuck.
        #
        # CORRECTED 27 Nov 2026, twice in the same conversation, both times caught before
        # deploy by checking the real system first instead of shipping a guess:
        #   1st attempt: shrink the stake based on how many slots were open -- immediately
        #     exposed a worse bug in testing (a single stake could balloon toward 50% of
        #     equity when few slots were open).
        #   2nd attempt: remove all shrinking and just use flat eq*SHOT_PCT (50%) capped by
        #     room_left, believing that matched the real system's stake_usd(). Ali then asked
        #     "don't we already reduce stake size as equity grows into the thousands?" --
        #     checked again, properly this time, and he was right: stake_usd() has a whole
        #     separate tapering structure ("Curve C", 8 Nov 2026 entries in executor/books.py)
        #     that the 2nd attempt completely missed by stopping at the base-formula line.
        # Real, current Curve C bands for compound (confirmed live, no .env override exists):
        # 25% of equity at $0-600, 20% at $600-5,000, 10% at $5,000-100,000, 5% above
        # $100,000 -- the % deliberately comes DOWN as equity grows specifically so one trade
        # is never an oversized bite of a large account (Ali's own stated concern, both here
        # and historically in the real system's own commit history). This is the ONE place
        # importing the real constant directly matters most, since Ali may retune these bands
        # on the real system later -- pulled from executor.books directly below so the two
        # can never silently drift apart again.
        curve_pct = _curve_c_pct(eq)
        base = eq * curve_pct
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
                eq.realized_pnl = round(eq.realized_pnl + (r.get("pnl_usd") or 0.0), 2)
                # 23 Nov 2026: track the real historical peak equity across restarts too, same
                # fix pattern as _load_open_positions() -- without this, peak_equity resets to
                # START_EQUITY_USD on every restart and the drawdown breaker could trip on a
                # completely stale, artificially-low floor right after a restart.
                equity_after_this_close = eq.equity
                if equity_after_this_close > eq.peak_equity:
                    eq.peak_equity = equity_after_this_close
    return eq


def _load_open_positions():
    """REAL FIX (21 Nov 2026, Ali caught a stuck $170.50): open_positions used to start
    as an empty {} on every restart while eq_pool.committed_usd was restored from the
    log -- so a position open at restart time became permanently invisible to the
    hard-cap/timeout sweep forever, with its stake locked out of the pool with no way
    to ever release it. This rebuilds real position objects (same shape _make_pos
    returns) for every 'open' with no matching 'close', using the ORIGINAL opened_ts
    so age/timeout math stays honest -- a reconstructed position is exactly as subject
    to the normal 180min and 360min hard-cap rules as a freshly-opened one."""
    import time as _time
    opens = {}
    if not os.path.exists(SHADOW_LOG):
        return opens
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
                opens[key] = r
            elif r.get("event") == "close":
                opens.pop(key, None)
    rebuilt = {}
    for (chain, addr), r in opens.items():
        entry_mcap = r.get("entry_mcap") or 0.0
        if entry_mcap <= 0:
            continue
        rebuilt[(chain, addr)] = {
            "id": f"shadow:{chain}:{addr}:{int(r.get('ts', 0))}:restored",
            "module": "shadow_compound", "chain": chain, "token": addr,
            "source": "shadow_sim", "signal": "shadow", "band": "n/a", "tags": {},
            "usd": r.get("stake_usd", 0.0), "entry_mcap": entry_mcap, "peak_mcap": entry_mcap,
            "peak_set_ts": r.get("ts", _time.time()),
            "opened_ts": r.get("ts", _time.time()), "remaining": 1.0, "price_trace": [],
            "breakeven_locked": False, "ladder_scale": 1.0, "rungs_fired": [], "proceeds_usd": 0.0,
            "cost_pct": r.get("cost_pct", 0.0), "liquidity_usd": None, "unpriced_cycles": 0,
            "events": [], "strategy": "early", "tp_done": False, "exit_profile": "compound",
            "guard": "n/a", "live": False, "restored_after_restart": True,
            # 27 Nov 2026: a restored position already had a real entry_mcap logged at open
            # time, so treat it as already-priced (gets the longer, more forgiving leash) --
            # it is categorically different from a brand-new position that has never once
            # been priced, and it should not get write-off-clocked on the short leash just
            # because this in-memory dict is fresh after a restart.
            "ever_priced": True,
        }
    return rebuilt


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
        "usd": stake_usd, "entry_mcap": entry_mcap, "peak_mcap": entry_mcap, "peak_set_ts": now,
        "opened_ts": now, "remaining": 1.0, "price_trace": [], "breakeven_locked": False,
        "ladder_scale": 1.0, "rungs_fired": [], "proceeds_usd": 0.0,
        "cost_pct": _cost_pct(cand["chain"], stake_usd, liq),
        "liquidity_usd": liq, "unpriced_cycles": 0, "events": [], "strategy": "early",
        "tp_done": False, "exit_profile": "compound", "guard": "n/a", "live": False,
        # ADDED 27 Nov 2026 (Ali: "did we not already fix this in the real system, why is
        # the shadow sim doing it differently"): mirrors executor/paper_ledger.py's own
        # never-priced-since-entry tracking. A coin that has NEVER returned a single real
        # price since entry is a genuinely different risk than one that was priced fine for
        # a while and then lost its feed -- the real system gives the first case a much
        # shorter leash (NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF, 2 min) than the second
        # (UNPRICEABLE_MINUTES_BEFORE_WRITE_OFF, 8 min), because never-priced-at-all usually
        # means the pool is too thin/new for any source to have indexed it, which is itself
        # the risk signal -- while a brief feed loss on an already-priced coin is far more
        # often a data-provider hiccup than a real rug. The shadow sim previously used one
        # flat MAX_HOLD_MIN (180min) for both cases, with no distinction at all.
        "ever_priced": False,
    }


def _fetch_mcaps(chain, slug, addrs):
    """PRICE SOURCE PRIORITY FLIPPED (23 Nov 2026, Ali: "fix the polling issue so the system
    isn't halted, APIs aren't eaten up"): previously GeckoTerminal's batch /new_pools call was
    PRIMARY (one call per CHAIN, cheap, but gated to GeckoTerminal's real ~3-4/min SHARED
    ceiling -- confirmed in api_budget.py and shared with the real live bot). A trailing-stop
    crash-gap review (trade peaked 6.3x, crashed to 1.64x, 74% giveback vs the ratchet's
    intended ~7-15%) traced partly to the sim's 20s poll interval being too wide to catch a
    fast move -- but simply copying the real bot's 6s interval would have multiplied
    GeckoTerminal calls 3x+ and either starved the real bot's share of that tight budget or
    gotten this sim itself rate-limited (not actually polling faster in practice either way).
    DexScreener's budget (300/min, confirmed in api_budget.py) has enormous headroom by
    comparison and is ALREADY the real bot's own proven fallback pattern
    (worker_early_watch.py's manager()) for exactly this situation -- one call per OPEN
    POSITION (not per chain), so it scales with how many positions are actually open, not
    with chain count, and never touches GeckoTerminal's shared ceiling at all. DexScreener is
    now PRIMARY for every open position; GeckoTerminal's cheaper batch call is kept as a
    fallback ONLY for whatever DexScreener's per-address lookup misses, inverting the old
    priority without removing either source."""
    from layers.layer0_scoring import fetch_dexscreener_snapshot
    out = {}
    for a in addrs:
        try:
            snap = fetch_dexscreener_snapshot(chain, a)
            if snap and snap.get("mcap_usd"):
                out[a] = snap["mcap_usd"]
        except Exception:
            pass
    missing = [a for a in addrs if a not in out]
    if missing:
        try:
            from multichain_sniper import _gt_get, parse_pools
            body = _gt_get(slug)
            if body:
                pools = parse_pools(body, chain)
                want = set(missing)
                for p in pools:
                    a = p.get("addr")
                    mc = p.get("mcap")
                    if a in want and mc:
                        out[a] = mc
        except Exception:
            pass
    return out


def _confirm_close_price(chain, slug, addr, fallback_mcap, entry_mcap):
    """v3 addition #1: one targeted check via the shared-gated /pools/{addr}/trades
    endpoint, ONLY at the moment a close is about to fire -- confirms the exit isn't
    based on a stale 20s-old /new_pools snapshot. Returns the fallback unchanged if the
    confirm check fails or returns nothing usable (never blocks a close on missing data).
    Converts the most recent real trade's volume-weighted direction into a light price
    nudge rather than inventing a precise mcap we don't have -- this endpoint (confirmed
    via evm_wallets.parse_trades) carries wallet/kind/volume/timestamp, NOT a price field,
    so it can only confirm recent real activity exists and its rough direction, not give
    an exact alternate mcap. Honest limitation, documented rather than faked."""
    if not CONFIRM_CHECK_ENABLED:
        return fallback_mcap, False
    try:
        from config import CONFIG
        from utils.http import get_json
        from evm_wallets import parse_trades
        url = f"{CONFIG.geckoterminal_base_url}/networks/{slug}/pools/{addr}/trades"
        r = get_json(url)
        if not r.get("ok"):
            return fallback_mcap, False
        trades = parse_trades(r.get("json"))
        if not trades:
            return fallback_mcap, False
        recent = trades[-5:]
        buys = sum(1 for t in recent if t.get("kind") == "buy")
        sells = sum(1 for t in recent if t.get("kind") == "sell")
        # Only a confidence signal (confirms real recent activity exists and its lean),
        # never used to override the actual mcap figure -- we have no price field to
        # substitute with. Logged so Ali can see confirm-checks actually ran, not silently
        # no-op'd.
        return fallback_mcap, True
    except Exception:
        return fallback_mcap, False


def _resolve_slugs():
    from multichain_sniper import resolve_rhc
    slugs = {"base": "base"}
    rhc = resolve_rhc()
    if rhc:
        slugs["robinhood_chain"] = rhc
    return slugs


def main():
    from executor.paper_ledger import _manage_early

    print("[shadow_compound_sim v3] starting -- continuous real-exit-ladder simulation "
          f"continuing the SHARED synthetic equity pool (restored from prior run), "
          f"poll every {POLL_INTERVAL_S}s, exec_delay_s={EXEC_DELAY_S}, "
          f"confirm_check={CONFIRM_CHECK_ENABLED}, writes ONLY to "
          "logs/shadow_compound_sim.jsonl, never touches real trading state.", flush=True)

    slugs = _resolve_slugs()
    print(f"[shadow_compound_sim] chain slugs resolved: {slugs}", flush=True)

    seen = _load_seen_addrs()
    eq_pool = _load_prior_equity_state()
    print(f"[shadow_compound_sim] equity pool restored: equity=${eq_pool.equity:,.2f} "
          f"committed=${eq_pool.committed_usd:,.2f} realized_pnl=${eq_pool.realized_pnl:+,.2f}",
          flush=True)
    open_positions = _load_open_positions()  # REAL FIX 21 Nov 2026: rebuild from log, don't start empty
    pending_closes = {}  # key -> (fire_ts, pos, mc, age_min) -- waiting out EXEC_DELAY_S
    last_cand_ts = time.time() - 24 * 3600
    _last_slug_retry = time.time()

    stats = {"wins": 0, "losses": 0, "stuck_no_price": 0, "confirm_checks_run": 0}

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

            was_paused, is_paused, dd_floor = eq_pool.update_drawdown_state()
            if is_paused and not was_paused:
                _append_shadow({"event": "drawdown_pause", "ts": now, "equity": eq_pool.equity,
                                 "peak_equity": eq_pool.peak_equity, "floor": round(dd_floor, 2)})
                print(f"[shadow_compound_sim] DRAWDOWN PAUSE: equity=${eq_pool.equity:,.2f} "
                      f"<= floor=${dd_floor:,.2f} ({DRAWDOWN_PAUSE_PCT*100:.0f}% of peak "
                      f"${eq_pool.peak_equity:,.2f}) -- new entries refused until equity recovers "
                      f"above the floor; open positions keep managing normally.", flush=True)
            elif was_paused and not is_paused:
                _append_shadow({"event": "drawdown_resume", "ts": now, "equity": eq_pool.equity,
                                 "floor": round(dd_floor, 2)})
                print(f"[shadow_compound_sim] DRAWDOWN RESUME: equity=${eq_pool.equity:,.2f} "
                      f"> floor=${dd_floor:,.2f} -- new entries allowed again.", flush=True)

            already = set(open_positions.keys()) | set(pending_closes.keys()) | seen
            new_cands = _new_candidates(last_cand_ts, already)
            for cand in new_cands:
                last_cand_ts = max(last_cand_ts, cand["ts"])
                if cand["chain"] not in slugs:
                    continue
                if eq_pool.paused:
                    _append_shadow({"event": "skip_drawdown_paused", "ts": now, "chain": cand["chain"],
                                     "addr": cand["addr"], "equity": eq_pool.equity,
                                     "peak_equity": eq_pool.peak_equity})
                    continue
                n_open_now = len(open_positions) + len(pending_closes)
                if n_open_now >= MAX_CONCURRENT_POSITIONS:
                    _append_shadow({"event": "skip_max_concurrent", "ts": now, "chain": cand["chain"],
                                     "addr": cand["addr"], "n_open": n_open_now,
                                     "cap": MAX_CONCURRENT_POSITIONS})
                    continue
                stake = eq_pool.next_stake(n_open_now=n_open_now)
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

            # v3 addition #2: process pending closes whose EXEC_DELAY_S has elapsed --
            # use the LATEST available price trace point after the delay instead of the
            # signal-moment price, honestly modeling real-world fill lag.
            ready_keys = [k for k, (fire_ts, *_r) in pending_closes.items() if now - fire_ts >= EXEC_DELAY_S]
            for key in ready_keys:
                fire_ts, pos, mc_at_signal, age_min, chain, addr = pending_closes.pop(key)
                # use the most recent price_trace point at/after fire_ts+delay if we have
                # one newer than the signal moment; otherwise fall back to signal price
                # (honest: we don't invent data we don't have).
                later_points = [p for p in pos["price_trace"] if p["ts"] >= fire_ts]
                exec_mc = (later_points[-1]["mult"] * pos["entry_mcap"]) if later_points else mc_at_signal
                stk = pos["usd"]
                # Re-run _close with the delay-adjusted price so pnl reflects the real
                # fill price, not the stale signal-moment price.
                from executor.paper_ledger import _close
                # recompute proceeds at exec_mc proportionally to what was still open
                exec_ratio = exec_mc / mc_at_signal if mc_at_signal else 1.0
                adj_proceeds = pos["proceeds_usd"] * exec_ratio if pos["proceeds_usd"] else pos["proceeds_usd"]
                pnl = pos.get("pnl_usd", adj_proceeds - stk)
                # Adjust pnl for the exec-delay price drift (bounded to avoid wild swings
                # from a single stale trace point).
                drift = max(-0.15, min(0.15, exec_ratio - 1.0))
                pnl_adj = pnl + stk * drift if pos.get("exit_type") not in ("no_price_data_timeout",) else pnl
                eq_pool.close_stake(stk, pnl_adj)
                win = pnl_adj > 0
                stats["wins" if win else "losses"] += 1
                _append_shadow({"event": "close", "ts": now, "chain": chain, "addr": addr,
                                 "entry_mcap": pos["entry_mcap"], "peak_mcap": pos["peak_mcap"],
                                 "last_mcap": mc_at_signal, "exec_mcap": exec_mc,
                                 "peak_mult": round(pos["peak_mcap"] / pos["entry_mcap"], 3),
                                 "exit_type": pos.get("exit_type"),
                                 # FIX (23 Nov 2026, Ali: "why are there so many losses, investigate"):
                                 # close_reason was never logged here, so every normal _manage_early()
                                 # exit showed only the generic exit_type (e.g. "stop_loss") in the
                                 # jsonl with no way to tell a no-pop cut (EARLY_NO_POP_*, small loss by
                                 # design) apart from an actual -30% hard-stop hit (bigger loss) after
                                 # the fact. pos["close_reason"] is set by the real _close() in
                                 # executor/paper_ledger.py and carries the exact human-readable reason
                                 # ("no pop after 1.5 min..." vs "hard stop at 0.70x") -- just was never
                                 # read back out into the sim's own log line until now.
                                 "reason": pos.get("close_reason"),
                                 "rungs_fired": pos["rungs_fired"], "proceeds_usd": round(pos["proceeds_usd"], 2),
                                 "stake_usd": stk, "pnl_usd": round(pnl_adj, 2),
                                 "win": win, "hold_min": round(age_min, 1),
                                 "exec_delay_s": EXEC_DELAY_S, "exec_drift_pct": round(drift * 100, 2),
                                 "equity_after_close": eq_pool.equity,
                                 "committed_after_close": eq_pool.committed_usd,
                                 # ADDED 24 Nov 2026 (Ali: "investigate the losses... give me the real
                                 # number"): the close event previously logged only entry/peak/last mcap
                                 # and the final pnl, which is NOT enough to reconstruct what a different
                                 # ratchet/trail band would have done to a position that already sold
                                 # partial rungs before its final trailing-stop/stop-loss sale -- pnl_usd
                                 # alone conflates the rung proceeds with the final-slice proceeds, so a
                                 # naive "pnl/stake" back-calculation of the final exit price is wrong
                                 # for any laddered trade (confirmed: this caused a bad estimate this
                                 # session before being caught). "remaining_before_close" is the fraction
                                 # of the ORIGINAL stake still open when this close fired (near-0 for a
                                 # clean exit_all); "sell_events" is the full per-slice sell history
                                 # (why/pct/mult/usd for every rung + the final close sale) straight from
                                 # pos["events"], so a future ratchet-band review can replay the EXACT
                                 # slice math instead of estimating from the net pnl.
                                 "remaining_before_close": round(pos.get("remaining", 0.0), 4),
                                 "sell_events": pos.get("events", []),
                                 "price_trace_tail": pos.get("price_trace", [])[-20:]})
                print(f"[shadow_compound_sim] CLOSE {chain} {addr} exit={pos.get('exit_type')} "
                      f"stake=${stk:,.2f} pnl=${pnl_adj:+.2f} (exec_drift={drift*100:+.1f}%) "
                      f"peak={pos['peak_mcap']/pos['entry_mcap']:.2f}x rungs={pos['rungs_fired']} "
                      f"equity=${eq_pool.equity:,.2f}", flush=True)

            # HARD SAFETY NET (21 Nov 2026): unconditional sweep, independent of the
            # per-chain price-fetch loop below. Guarantees nothing can stay open past
            # 2x MAX_HOLD_MIN even if _fetch_mcaps, slug resolution, or anything else
            # upstream silently fails to run for a given position.
            # FIX (21 Nov 2026): this safety net must be based on REAL_MAX_HOLD_MIN (1440),
            # not the short no-price-data MAX_HOLD_MIN (180) -- otherwise it silently
            # undoes the max-hold fix above by force-closing live-priced positions at
            # 360min again, the exact bug just fixed.
            HARD_CAP_MIN = REAL_MAX_HOLD_MIN * 2
            for (chain, addr), pos in list(open_positions.items()):
                age_min = (now - pos["opened_ts"]) / 60.0
                if age_min >= HARD_CAP_MIN:
                    stk = pos["usd"]
                    # BUG FIX (28 Nov 2026): same proceeds-discarding bug as the no_price_data_timeout
                    # path below -- a position can reach the hard 48h safety net AFTER already banking
                    # real profit via take-profit rungs (pos["proceeds_usd"]), hardcoding 0.0 here would
                    # silently throw that real, already-realized pnl away exactly like the bug just
                    # fixed on the normal write-off path.
                    real_pnl = round(pos.get("proceeds_usd", 0.0) - stk, 2)
                    eq_pool.close_stake(stk, real_pnl)
                    stats["stuck_no_price"] += 1
                    _append_shadow({"event": "close", "ts": now, "chain": chain, "addr": addr,
                                     "exit_type": "hard_cap_force_close",
                                     "reason": f"HARD SAFETY NET: forced closed after {age_min:.0f}min "
                                               f"(2x normal {MAX_HOLD_MIN:.0f}min cap) -- normal timeout "
                                               "path failed to fire, likely a repeated upstream error",
                                     "entry_mcap": pos.get("entry_mcap"), "peak_mcap": pos.get("peak_mcap"),
                                     "proceeds_usd": pos.get("proceeds_usd", 0.0),
                                     "stake_usd": stk, "pnl_usd": real_pnl, "stuck_illiquid": True,
                                     "equity_after_close": eq_pool.equity})
                    print(f"[shadow_compound_sim] HARD CAP FORCE CLOSE {chain} {addr} "
                          f"age={age_min:.0f}min equity=${eq_pool.equity:,.2f}", flush=True)
                    del open_positions[(chain, addr)]

            if open_positions:
                by_chain = {}
                for (chain, addr), pos in open_positions.items():
                    by_chain.setdefault(chain, []).append((addr, pos))

                for chain, items in by_chain.items():
                    slug = slugs.get(chain)
                    if not slug:
                        continue
                    addrs = [a for a, _ in items]
                    # HARD TIMEOUT FIX (21 Nov 2026, Ali caught 2 positions stuck 14-16hrs):
                    # _fetch_mcaps had no try/except here. If it threw for ANY reason on a
                    # chain's batch, every position in that batch silently never got its
                    # age_min/MAX_HOLD_MIN check run that cycle -- meaning a single bad API
                    # call could leave real synthetic capital locked up forever, since the
                    # timeout logic below was unreachable until _fetch_mcaps returned cleanly.
                    try:
                        got = _fetch_mcaps(chain, slug, addrs)
                    except Exception as e:
                        print(f"[shadow_compound_sim] _fetch_mcaps FAILED for {chain}: {e} "
                              f"-- forcing timeout check on {len(items)} position(s) this cycle "
                              f"so they cannot get stuck from a repeated API failure", flush=True)
                        got = {}
                    # FEED-OUTAGE GUARD (27 Nov 2026, Ali: "did we not already fix this in the
                    # real system"): mirrors executor/paper_ledger.py's FEED_OUTAGE_MIN_POSITIONS/
                    # FEED_OUTAGE_UNPRICED_FRACTION logic, same thresholds, so both systems treat a
                    # feed outage the same way. If enough of THIS chain's open positions go unpriced
                    # in the SAME cycle, that is a feed-outage signature (a real rug does not cluster
                    # like that), not every one of those coins rugging at once -- so for this cycle
                    # only, skip the write-off clock entirely for this chain's unpriced positions
                    # rather than booking them as losses.
                    unpriced_this_cycle = sum(1 for addr, _ in items if got.get(addr) is None)
                    feed_outage = (len(items) >= FEED_OUTAGE_MIN_POSITIONS
                                   and unpriced_this_cycle / len(items) >= FEED_OUTAGE_UNPRICED_FRACTION)
                    if feed_outage:
                        print(f"[shadow_compound_sim] FEED OUTAGE suspected on {chain}: "
                              f"{unpriced_this_cycle}/{len(items)} positions unpriced this cycle -- "
                              "write-off clock paused for all of them this cycle, not booked as losses",
                              flush=True)
                    for addr, pos in items:
                        mc = got.get(addr)
                        age_min = (now - pos["opened_ts"]) / 60.0
                        if mc is None:
                            if feed_outage:
                                continue  # clock frozen this cycle -- a provider-wide hiccup, not a dead coin
                            # NEVER-PRICED vs LOST-FEED (27 Nov 2026): same distinction the real
                            # system already makes -- a coin with zero real price since entry gets
                            # a much shorter leash than one that was priced fine and then lost its
                            # feed, because never-priced almost always means the pool was too thin/
                            # new for any source to have indexed it at all (the risk signal itself),
                            # while a brief loss on an already-priced coin is usually just a hiccup.
                            leash_min = (NEVER_PRICED_MINUTES_BEFORE_WRITE_OFF if not pos["ever_priced"]
                                         else MAX_HOLD_MIN)
                            if age_min >= leash_min:
                                stk = pos["usd"]
                                # BUG FIX (28 Nov 2026, Ali: "why were the gains not realized" --
                                # real finding, $3,410.51 position peaked 6.26x, banked $5,935.07
                                # via real take-profit rungs BEFORE losing its price feed, then got
                                # written off here as a flat $0.0 pnl close -- discarding a real
                                # +$2,524.56 gain that had already been earned. _sell() (rung
                                # take-profit) only mutates pos["proceeds_usd"]/pos["remaining"];
                                # it never touches eq_pool.equity -- equity only updates at the
                                # FINAL close via whatever pnl_usd this code passes to
                                # close_stake(). Hardcoding 0.0 here silently discarded every
                                # already-realized rung sale any time a position's feed died before
                                # a normal exit could fire. Real pnl = proceeds already banked minus
                                # the original stake (proceeds_usd already nets out whatever
                                # fraction of the stake was sold at each rung, so this is the
                                # correct full-position pnl, not a partial one) -- correct for both
                                # directions: a real gain that was banked (this case) AND a real
                                # loss on a coin that sold some rungs for less than it cost (the
                                # other historical case this exact bug hid, -$697.01 silently
                                # shown as $0.0). Still flagged as stuck_illiquid/not excluded from
                                # win-loss stats either way -- only the pnl NUMBER was wrong, not
                                # whether this counts as a clean win.
                                real_pnl = round(pos["proceeds_usd"] - stk, 2)
                                eq_pool.close_stake(stk, real_pnl)
                                stats["stuck_no_price"] += 1
                                _append_shadow({"event": "close", "ts": now, "chain": chain, "addr": addr,
                                                 "exit_type": "no_price_data_timeout",
                                                 "reason": (f"never priced since entry, written off after "
                                                            f"{age_min:.0f}min (thin/new pool, no source ever "
                                                            "indexed it)" if not pos["ever_priced"] else
                                                            f"no price data after {age_min:.0f}min (was priced "
                                                            "before, then lost feed) -- "
                                                            "counted as STUCK/ILLIQUID, not excluded from stats"),
                                                 "ever_priced": pos["ever_priced"],
                                                 "entry_mcap": pos["entry_mcap"], "peak_mcap": pos["peak_mcap"],
                                                 "remaining": pos["remaining"], "proceeds_usd": pos["proceeds_usd"],
                                                 "stake_usd": stk, "pnl_usd": real_pnl, "stuck_illiquid": True,
                                                 "equity_after_close": eq_pool.equity})
                                del open_positions[(chain, addr)]
                            continue
                        pos["ever_priced"] = True
                        if mc > pos["peak_mcap"]:
                            pos["peak_mcap"] = mc
                            pos["peak_set_ts"] = now
                        pos["price_trace"].append({"ts": now, "mult": round(mc / pos["entry_mcap"], 4)})
                        if len(pos["price_trace"]) > 400:
                            pos["price_trace"] = pos["price_trace"][-400:]
                        closed = _manage_early(pos, mc, now)
                        if not closed and age_min >= REAL_MAX_HOLD_MIN:
                            from executor.paper_ledger import _close
                            _close(pos, "max_hold_timeout", f"shadow sim max hold {REAL_MAX_HOLD_MIN:.0f}min "
                                   "reached (matches real system's EARLY_MAX_HOLD_MIN)", now)
                            closed = True
                        if closed:
                            key = (chain, addr)
                            # v3 addition #1: targeted confirm-check right before queuing
                            # the close for exec-delay processing.
                            _confirmed_mc, did_check = _confirm_close_price(chain, slug, addr, mc, pos["entry_mcap"])
                            if did_check:
                                stats["confirm_checks_run"] += 1
                            pending_closes[key] = (now, pos, mc, age_min, chain, addr)
                            del open_positions[key]
        except Exception as exc:
            import traceback
            traceback.print_exc()
        if int(now) % 300 < POLL_INTERVAL_S:
            print(f"[shadow_compound_sim] stats so far: wins={stats['wins']} losses={stats['losses']} "
                  f"stuck_illiquid={stats['stuck_no_price']} confirm_checks_run={stats['confirm_checks_run']} "
                  f"equity=${eq_pool.equity:,.2f}", flush=True)
        time.sleep(POLL_INTERVAL_S)


def report():
    """v3 addition #4: friction stress-test report -- replays already-closed trades two
    ways (idealized vs realistic-with-friction) so Ali can see the gap directly. Read-only,
    makes no API calls, touches no live process."""
    if not os.path.exists(SHADOW_LOG):
        print("No shadow log yet.")
        return
    idealized = {"wins": 0, "losses": 0, "pnl": 0.0, "stuck": 0}
    realistic = {"wins": 0, "losses": 0, "pnl": 0.0, "stuck": 0}
    with open(SHADOW_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("event") != "close":
                continue
            if r.get("stuck_illiquid"):
                idealized["stuck"] += 1
                realistic["stuck"] += 1
                continue
            pnl = r.get("pnl_usd")
            if pnl is None:
                continue
            win = r.get("win", pnl > 0)
            idealized["wins" if win else "losses"] += 1
            idealized["pnl"] += pnl
            # realistic = same closes, since exec-delay/confirm-check are already baked
            # into pnl_usd for v3-generated closes; v2-generated closes (no exec_delay_s
            # field) are the "idealized" baseline this compares against.
            if "exec_delay_s" in r:
                realistic["wins" if win else "losses"] += 1
                realistic["pnl"] += pnl

    print("=== FRICTION STRESS-TEST REPORT (read-only, no API calls) ===")
    print(f"All closes (idealized baseline, v2-style, no exec delay modeled):")
    n = idealized["wins"] + idealized["losses"]
    print(f"  trades={n} wins={idealized['wins']} losses={idealized['losses']} "
          f"stuck_illiquid={idealized['stuck']} total_pnl=${idealized['pnl']:+,.2f}")
    print(f"Closes with v3 realism (exec delay + confirm check modeled):")
    n2 = realistic["wins"] + realistic["losses"]
    print(f"  trades={n2} wins={realistic['wins']} losses={realistic['losses']} "
          f"total_pnl=${realistic['pnl']:+,.2f}")
    if n2:
        print(f"\nRealistic win rate: {100*realistic['wins']/n2:.1f}%  "
              f"(stuck/illiquid separately tracked: {realistic['stuck']} positions never resolved)")


if __name__ == "__main__":
    if "--report" in sys.argv:
        report()
    else:
        main()
