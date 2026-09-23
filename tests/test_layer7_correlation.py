from datetime import datetime, timezone
from layers.layer7_correlation import AlertEvent, detect_mega_alerts


def _t(hhmm):
    return datetime.fromisoformat(f"2026-09-06T{hhmm}:00+00:00")


def test_mega_alert_when_two_layers_fire_within_window():
    events = [
        AlertEvent("TOKEN_A", "layer0", _t("14:00")),
        AlertEvent("TOKEN_A", "layer2", _t("14:25")),
    ]
    mega = detect_mega_alerts(events)
    assert len(mega) == 1
    assert mega[0].token == "TOKEN_A"
    assert set(mega[0].layers) == {"layer0", "layer2"}


def test_no_mega_alert_for_single_layer_firing_twice():
    events = [
        AlertEvent("TOKEN_A", "layer0", _t("14:00")),
        AlertEvent("TOKEN_A", "layer0", _t("14:10")),
    ]
    assert detect_mega_alerts(events) == []


def test_no_mega_alert_outside_window():
    events = [
        AlertEvent("TOKEN_A", "layer0", _t("14:00")),
        AlertEvent("TOKEN_A", "layer2", _t("15:30")),  # 90 min later, outside 60-min window
    ]
    assert detect_mega_alerts(events) == []


def test_three_layers_reported_together_not_as_separate_pairs():
    events = [
        AlertEvent("TOKEN_A", "layer0", _t("14:00")),
        AlertEvent("TOKEN_A", "layer2", _t("14:10")),
        AlertEvent("TOKEN_A", "layer9", _t("14:20")),
    ]
    mega = detect_mega_alerts(events)
    assert len(mega) == 1
    assert set(mega[0].layers) == {"layer0", "layer2", "layer9"}
