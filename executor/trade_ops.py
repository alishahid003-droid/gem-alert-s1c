"""
Trade reliability + Telegram ops alerts -- GO_LIVE_CHECKLIST 5.3 / 5.5 / 5.6
(Sept 30 2026).

5.3 retry: a buy or sell that fails BEFORE anything was sent (quote failed,
    swap-tx build failed, price fetch failed, RPC busy) is retried ONCE with a
    fresh quote. Never retried: anything with a tx signature ("sent but could
    not confirm" -- a retry could double-buy), safety refusals (sellability,
    honeypot, guards), and the disabled/no-key refusal.
5.5 alerts: every real buy/sell attempt (success or failure) goes to
    Telegram; a runner whose heartbeat is stale gets one alert per 2 h.
5.6 daily summary: once per UTC day -- paper win rate, real trades, P&L,
    MadeOnSol usage.
All Telegram sends are best-effort and never raise into a trade path.
"""
import functools
import time
from typing import Optional

import state

TRANSIENT_MARKERS = (
    "quote failed", "swap-tx build failed", "could not fetch a live", "no swapTransaction",
    "timed out", "timeout", "connection", "429", "503", "502",
)
NO_RETRY_MARKERS = ("refused", "honeypot", "unsellable", "EXECUTION_ENABLED", "no execution wallet key",
                    "not installed", "no sell path", "sent but could not confirm", "sendTransaction",
                    "failed to sign", "broadcast")
QUIET_MARKERS = ("EXECUTION_ENABLED is not", "no execution wallet key", "refused before buying")

STALE_AFTER_SECONDS = {"fast-watch": 5 * 60, "poll-fast": 40 * 60, "poll-madeonsol": 45 * 60, "poll-slow": 90 * 60}
STALL_ALERT_EVERY_SECONDS = 2 * 3600


def _send(text: str) -> dict:
    try:
        import telegram_alert
        return telegram_alert.send_telegram_message(text)
    except Exception as e:  # noqa: BLE001 -- alerts never break trading
        return {"sent": False, "reason": str(e)}


def is_transient(result) -> bool:
    if getattr(result, "ok", False) or getattr(result, "tx_signature", None):
        return False
    reason = str(getattr(result, "reason", "") or "")
    low = reason.lower()
    if any(m.lower() in low for m in NO_RETRY_MARKERS):
        return False
    return any(m.lower() in low for m in TRANSIENT_MARKERS)


def notify_trade(side: str, chain: str, token: str, result, extra: str = "") -> Optional[dict]:
    reason = str(getattr(result, "reason", "") or "")
    if not getattr(result, "ok", False) and any(m in reason for m in QUIET_MARKERS):
        return None
    if getattr(result, "ok", False):
        head = f"✅ REAL {side.upper()} {chain}"
        usd = getattr(result, "filled_usd", None)
        body = f"${usd:,.2f}" if isinstance(usd, (int, float)) else ""
    else:
        head = f"❌ REAL {side.upper()} FAILED {chain}"
        body = reason[:300]
    sig = getattr(result, "tx_signature", None)
    lines = [head, f"`{token}`", body]
    if extra:
        lines.append(extra)
    if sig:
        lines.append(f"tx: `{sig}`")
    return _send("\n".join(x for x in lines if x))


def hardened(side: str, chain: Optional[str] = None):
    """Wraps a swap_executor buy/sell: retry once on transient pre-send
    failure, then notify Telegram of the final result."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            retried = False
            if is_transient(result):
                time.sleep(1.5)
                result = fn(*args, **kwargs)
                retried = True
            ch = chain or kwargs.get("chain") or (args[0] if args else "?")
            tok = (kwargs.get("token_address") or kwargs.get("token_mint")
                   or (args[1] if chain is None and len(args) > 1 else (args[0] if args else "?")))
            notify_trade(side, ch, tok, result, "retried once with a fresh quote" if retried else "")
            return result
        wrapper.__wrapped_once__ = True
        return wrapper
    return deco


def check_stalled_runners(now: Optional[float] = None) -> list:
    now = now or time.time()
    beats = state.get_runner_heartbeats()
    last = state.get_value("ops:stall_alerts") or {}
    stalled = []
    for name, limit in STALE_AFTER_SECONDS.items():
        b = beats.get(name)
        if not b:
            continue            # never ran here -- not a stall
        age = now - float(b.get("ts") or 0)
        if age > limit:
            stalled.append(name)
            if now - float(last.get(name) or 0) > STALL_ALERT_EVERY_SECONDS:
                _send(f"⚠️ Runner *{name}* has not run for {age / 60:.0f} min "
                      f"(last seen on {b.get('where') or '?'}). Check the PC / cron-job.org.")
                last[name] = now
    state.set_value("ops:stall_alerts", last)
    return stalled


def daily_summary_text(now: Optional[float] = None) -> str:
    from executor import paper_ledger
    now = now or time.time()
    since = now - 24 * 3600
    sb = paper_ledger.scoreboard(since_ts=since)
    o = sb.get("overall") or {}
    trades = [t for t in state.get_trade_log(limit=500) if (t.get("ts") or 0) >= since]
    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") == "sell"]
    ok_b = sum(1 for t in buys if t.get("ok"))
    ok_s = sum(1 for t in sells if t.get("ok"))
    spent = sum(float(t.get("usd_amount") or 0) for t in buys if t.get("ok"))
    back = sum(float(t.get("usd_amount") or 0) for t in sells if t.get("ok"))
    ms = state.madeonsol_pacing_status()
    lines = [
        "📊 *Daily summary (last 24 h)*",
        f"Paper: {o.get('wins', 0)}/{o.get('n', 0)} wins ({(o.get('win_rate') or 0) * 100:.0f}%), "
        f"avg {o.get('expectancy_pct', 0) or 0:+.1f}% per trade, {sb.get('open_count', 0)} open",
        f"Real: {ok_b}/{len(buys)} buys ok, {ok_s}/{len(sells)} sells ok, "
        f"in ${spent:,.2f} / out ${back:,.2f}",
        f"MadeOnSol: {ms.get('used')}/{ms.get('budget')} calls today",
    ]
    try:
        from executor.sprint import status_line
        sl = status_line()
        if sl:
            lines.append(sl)
    except Exception:  # noqa: BLE001
        pass
    by_sig = sb.get("by_signal") or {}
    if by_sig:
        best = sorted(by_sig.items(), key=lambda kv: -(kv[1].get("n") or 0))[:4]
        lines.append("By signal: " + ", ".join(
            f"{k} {v.get('wins', 0)}/{v.get('n', 0)}" for k, v in best))
    return "\n".join(lines)


def maybe_send_daily_summary(now: Optional[float] = None) -> bool:
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if state.get_value("ops:daily_summary_day") == day:
        return False
    state.set_value("ops:daily_summary_day", day)
    _send(daily_summary_text(now))
    return True
