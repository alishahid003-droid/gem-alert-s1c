"""Dev-launch alert: when a creator from our self-built good-dev scoreboard (dev_rank.py) or the dev list launches a coin,
send a Telegram alert and put it on the dashboard Alerts tab (module DEV LAUNCH). Alert only - never trades by itself;
the same signal also adds a 'good_dev' confirmation to the coin's entry score."""
import time

KEY_SEEN = "dev_alert_seen_v1"
COOLDOWN_S = 600          # one alert per dev per 10 min (spammers launch many)


def maybe_alert(state, creator, mint, symbol="", name="", reason="good_dev", send=None, now=None) -> bool:
    now = now or time.time()
    if not creator or not mint:
        return False
    seen = {k: v for k, v in (state.get_value(KEY_SEEN) or {}).items() if now - v < 86400}
    if mint in seen or now - seen.get("dev:" + creator, 0) < COOLDOWN_S:
        return False
    seen[mint] = now
    seen["dev:" + creator] = now
    state.set_value(KEY_SEEN, seen)
    st = (state.get_value("dev_stats_v1") or {}).get(creator)
    hist = f"won {st[1]} of {st[0]} past launches" if st else "on our dev list"
    sym = symbol or name or mint[:6]
    headline = f"DEV LAUNCH: {'proven dev' if reason == 'good_dev' else 'listed dev'} just launched {sym} ({hist})"
    tags = {"Deployer": f"{creator[:6]}..{creator[-4:]} ({hist})", "Chain": "solana"}
    try:
        state.log_full_alert("early_dev_list", "solana", sym, mint, headline, tags, ts=now)
    except Exception:  # noqa: BLE001
        pass
    if send is None:
        def send(h, t):
            import telegram_alert as ta
            a = ta.Alert(sym, mint, "solana", h)
            a.module_label = "\U0001F9D1\u200D\U0001F4BB DEV LAUNCH"
            for k, v in t.items():
                a.set_tag(k, v)
            return ta.send_alert(a)
    try:
        send(headline, tags)
    except Exception:  # noqa: BLE001
        pass
    return True
