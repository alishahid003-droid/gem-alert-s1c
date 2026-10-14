"""FAST router (14 Oct 2026): every free-data early signal is routed into the FOUR money streams.

Streams (each is a book with its own balance, target and exits, see executor/books.py):
  sprint   "bills" stream  $100 -> $10,000 in 96h   profile fast_sprint : bank early, 40% runner, tight window
  core1    "Core 2"        -> $125k-150k in 30 days profile fast_core   : coins 2-20 min old that keep gaining buyers
  core2    "Core 3"        -> $1M-1.5M in 180 days  profile fast_moon   : tiny lottery shots, 80% runner
  compound "in/out" stream  small runners 2x-3x, in and out profile compound (existing quick exits)

Rule: a coin gets AT MOST ONE moonshot book (moon > sprint > core) plus, if it qualifies, ONE compound position.
Paper opens every routed signal (evidence; capacity FAST_PAPER_MAX_OPEN). LIVE is a separate, stricter gate in
executor/pumpportal_exec.allowed and only fires when EARLY_FREE_CONFIRM=live; only fast_sprint can be live.
Pure functions + one ledger lookup; no network.
"""
import os
from typing import List, Optional, Tuple


def _f(name: str, default: float) -> float:
    try:
        v = os.environ.get(name, "").strip()
        return float(v) if v else default
    except ValueError:
        return default


# profile -> (book id = ledger module, env name for stake, default stake USD)
STREAMS = {
    "fast_moon": ("core2", "FAST_MOON_STAKE_USD", 2.0),
    "fast_sprint": ("sprint", "FAST_SPRINT_STAKE_USD", 5.0),
    "fast_core": ("core1", "FAST_CORE_STAKE_USD", 4.0),
    "compound": ("compound", "FAST_COMPOUND_STAKE_USD", 4.0),
}


def route(decision: dict) -> List[dict]:
    """[{profile, book, stake}] for one decision; [] when it fits nowhere."""
    age = float(decision.get("age_s") or 0.0)
    mc = float(decision.get("entry_mcap_sol") or decision.get("mcap_sol") or 0.0)
    conf = list(decision.get("confirmations") or [])
    n = len(conf)
    famous = "famous_name" in conf
    profiles = []
    if age <= _f("FAST_MOON_MAX_AGE_S", 60) and mc <= _f("FAST_MOON_MAX_MCAP_SOL", 35) and famous and n >= 3:
        profiles.append("fast_moon")                                # rare, strongest evidence: lottery shot for the 180-day book
    elif age <= _f("FAST_SPRINT_MAX_AGE_S", 120) and mc <= _f("FAST_SPRINT_MAX_MCAP_SOL", 55):
        profiles.append("fast_sprint")
    elif age <= _f("FAST_CORE_MAX_AGE_S", 1200) and mc <= _f("FAST_CORE_MAX_MCAP_SOL", 130):
        profiles.append("fast_core")
    if profiles and n >= 2 and mc <= _f("FAST_COMPOUND_MAX_MCAP_SOL", 130):
        profiles.append("compound")                                 # in/out stream rides the same coin with quick exits
    out = []
    for p in profiles:
        book, env, dflt = STREAMS[p]
        stake = _f(env, dflt)
        if stake > 0:
            out.append({"profile": p, "book": book, "stake": stake})
    return out


def assign(decision: dict) -> Optional[str]:
    """Primary profile (first route) or None."""
    r = route(decision)
    return r[0]["profile"] if r else None


def score(d) -> int:
    """Entry score (14 Oct). Many weak checks add up; no single check decides. Real money needs FAST_LIVE_MIN_SCORE (default 5).
    Weights are a first guess from 86 logged signals: re-fit them after ~24 h of logged scores."""
    d = d or {}
    c = set(d.get("confirmations") or [])
    s = 0
    for k, w in (("famous_name", 3), ("smart_wallet", 3), ("narrative", 2), ("narrative_first", 2), ("dev_list", 2), ("repeat_grad", 2)):
        if k in c:
            s += w
    s += 1 if "pressure_free" in c else 0
    s += 1 if "velocity_free" in c else 0
    fb, fs = d.get("fb") or 0, d.get("fs") or 0
    if fb or fs:
        r = fs / max(fb, 1)
        s += 2 if r < .3 else (1 if r < .45 else (-2 if r > .6 else 0))
        s += 1 if 40 <= fb <= 80 else (-1 if fb > 80 else 0)
    m = d.get("mcap_sol") or d.get("entry_mcap_sol") or 0
    s += 1 if 30 <= m < 45 else (-2 if m >= 45 else 0)
    return s


def live_ok(decision) -> bool:
    """Real money needs a high enough SCORE (several checks agreeing), never one check. FAST_LIVE_MIN_SCORE=-99 turns it off."""
    return score(decision) >= int(float(os.environ.get("FAST_LIVE_MIN_SCORE", "5")))


def live_lane(profile: str, decision=None) -> str:
    """Name used by pumpportal_exec.allowed / EARLY_LIVE_LANES: only fast_sprint can ever go live (as 'sprint'),
    and only when its entry score clears the bar."""
    if profile != "fast_sprint":
        return "never_live"
    if decision is not None and not live_ok(decision):
        return "never_live"
    return "sprint"


def open_fast() -> list:
    from executor import paper_ledger
    return [p for p in paper_ledger._open().values() if str((p.get("tags") or {}).get("fast")) == "1"]


def paper_gate() -> Tuple[bool, str]:
    n = len(open_fast())
    cap = int(_f("FAST_PAPER_MAX_OPEN", 60))
    return (n < cap), (f"fast paper capacity {n}/{cap}" if n >= cap else "ok")
