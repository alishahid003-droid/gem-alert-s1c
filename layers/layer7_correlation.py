"""
Layer 7 -- Cross-layer correlation.

Pure logic, no new API/tool, per spec. If 2+ *distinct* layers fire on the
same token within ~30-60 minutes of each other, tag it a top-priority
"MEGA-ALERT" carrying the count of contributing layers and the time window.

The scheduler is expected to log every alert it sends as an AlertEvent
(token, layer, timestamp) into a short rolling window (state.py can hold
this the same way it holds Layer 6/8/9's cross-cycle state) and call
detect_mega_alerts() against that window each cycle.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List
from collections import defaultdict

CORRELATION_WINDOW = timedelta(minutes=60)  # spec says "~30-60 min" -- using the wider bound
MIN_DISTINCT_LAYERS = 2


@dataclass
class AlertEvent:
    token: str
    layer: str
    timestamp: datetime


@dataclass
class MegaAlert:
    token: str
    layers: List[str]
    window_minutes: float


def _parse_time(ts):
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def detect_mega_alerts(events: List[AlertEvent], window: timedelta = CORRELATION_WINDOW,
                        min_layers: int = MIN_DISTINCT_LAYERS) -> List[MegaAlert]:
    by_token = defaultdict(list)
    for e in events:
        by_token[e.token].append(e)

    mega_alerts = []
    for token, evs in by_token.items():
        evs = sorted(evs, key=lambda e: e.timestamp)
        for i, anchor in enumerate(evs):
            window_layers = {anchor.layer}
            latest_ts = anchor.timestamp
            for other in evs[i + 1:]:
                if other.timestamp - anchor.timestamp <= window:
                    window_layers.add(other.layer)
                    latest_ts = other.timestamp
                else:
                    break
            if len(window_layers) >= min_layers:
                mega_alerts.append(MegaAlert(
                    token=token,
                    layers=sorted(window_layers),
                    window_minutes=(latest_ts - anchor.timestamp).total_seconds() / 60,
                ))

    # keep only the richest (most layers) mega-alert per token
    best = {}
    for m in mega_alerts:
        cur = best.get(m.token)
        if not cur or len(m.layers) > len(cur.layers):
            best[m.token] = m
    return list(best.values())
