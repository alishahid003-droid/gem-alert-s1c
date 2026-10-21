import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from layers import layer21_celeb_signal_watch as l21


def test_scan_message_hit_name_plus_mint():
    text = "Trump just transferred to a new wallet 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
    hit = l21.scan_message("whale_insider", text, msg_id=1, msg_ts=1.0)
    assert hit is not None
    assert "Trump" in hit["names"] or any(n.lower() == "trump" for n in hit["names"])
    assert hit["mints"]


def test_scan_message_hit_name_plus_action_no_mint():
    text = "Elon just bought into a new pump.fun coin minutes ago"
    hit = l21.scan_message("lookonchain", text)
    assert hit is not None
    assert hit["mints"] == []


def test_scan_message_name_only_no_hit():
    text = "Everyone is talking about Elon today, no on-chain activity though"
    hit = l21.scan_message("lookonchain", text)
    assert hit is None


def test_scan_message_no_name_no_hit():
    text = "Some random wallet 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU just launched a coin"
    hit = l21.scan_message("spotonchain", text)
    assert hit is None


def test_format_alert_never_claims_involvement():
    hit = {"channel": "lookonchain", "names": ["Musk"], "mints": ["abc123"], "text": "Musk wallet funded a new mint"}
    msg = l21.format_alert(hit)
    assert "unverified" in msg.lower()
    assert "not a confirmation" in msg.lower()


def test_run_cycle_fails_closed_without_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    result = l21.run_cycle()
    assert "disabled" in result


def test_run_cycle_with_fake_client():
    class FakeMsg:
        def __init__(self, text, id_):
            self.text = text
            self.id = id_
            self.date = None

    class FakeClient:
        def iter_messages(self, channel, limit=30):
            return [FakeMsg("Trump sent funds to 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", 1)]

    alerts = []
    result = l21.run_cycle(client=FakeClient(), send_alert=alerts.append)
    assert "hit(s)" in result
    assert len(alerts) >= len(l21.CHANNELS)  # same fake client reused per channel
    assert "CELEB-SIGNAL" in alerts[0]
