import requests

from telegram_alert import Alert, send_telegram_message


def test_alert_render_tag_order_and_content():
    a = Alert("SGEM", "0xabc123", "base", "High-risk momentum spike")
    a.set_tag("Chain", "Base") \
     .set_tag("Score", "HIGH-RISK MOMENTUM") \
     .set_tag("Convergence", "3 wallets") \
     .set_tag("Backing", "needs-verification")
    text = a.render()
    assert "[Chain: Base]" in text
    assert "[Score: HIGH-RISK MOMENTUM]" in text
    # order must follow the spec's tag order: Chain before Score before Convergence before Backing
    assert text.index("[Chain:") < text.index("[Score:") < text.index("[Convergence:") < text.index("[Backing:")


def test_alert_omits_absent_tags():
    a = Alert("X", "addr", "solana", "headline")
    a.set_tag("Chain", "Solana")
    text = a.render()
    assert "[SELL:" not in text
    assert "[MEGA-ALERT:" not in text


def test_send_telegram_message_survives_network_failure(monkeypatch):
    # Real bug caught live Sept 28 2026: a DNS/network-level failure here
    # used to raise uncaught all the way up through run_poll_fast and crash
    # the whole poll cycle -- before it ever reached Stage1/Stage2's
    # buy-trigger checks. Must fail closed like every other network call
    # in this codebase, not take the whole cycle down with it.
    import telegram_alert
    monkeypatch.setattr(telegram_alert.CONFIG, "telegram_bot_token", "fake_token")
    monkeypatch.setattr(telegram_alert.CONFIG, "telegram_chat_id", "fake_chat")

    def _raise(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Failed to resolve 'api.telegram.org'")

    monkeypatch.setattr(requests, "post", _raise)
    result = send_telegram_message("test alert")
    assert result["sent"] is False
    assert "network-level failure" in result["reason"]


def test_send_telegram_message_still_reports_missing_config(monkeypatch):
    import telegram_alert
    monkeypatch.setattr(telegram_alert.CONFIG, "telegram_bot_token", None)
    result = send_telegram_message("test alert")
    assert result["sent"] is False
    assert "not configured" in result["reason"]
