"""One-off live diagnostic: why isn't the dashboard showing roster
buys/theses even though Ali sees his Following list active on the Fomo
app right now? Three things this checks, in order:

1. Is Layer 13 even running on a schedule -- state's fomo_alerts_since
   cursor tells us when it last successfully advanced. If that's old or
   missing, the scheduled task (run_madeonsol_hidden.vbs -> `python
   scheduler.py --poll-madeonsol`) likely isn't actually running
   periodically -- it may have only been run once by hand.
2. Pulls the last 100 raw alerts straight from fomoapi.io (bypassing the
   stored cursor) and prints each one's real trader handle + alert type.
3. For every alert, shows whether roster.py's _match_roster() actually
   recognizes that handle -- if real activity from your 38 tracked names
   is in the raw feed but shows "NO MATCH", that's a name-transcription
   mismatch, not a real detection failure.

Run: python diag_fomo_roster.py
"""
from dotenv import load_dotenv
load_dotenv()

import state
from layers.layer13_fomo_copytrade import fetch_fomo_alerts, _match_roster
from layers.roster import SELL_WATCH_ROSTER

print("=" * 70)
print("1) Last time Layer 13 successfully advanced its cursor:")
since = state.get_fomo_alerts_since()
print(f"   fomo_alerts_since = {since!r}")
if since is None:
    print("   -> No cursor at all. Layer 13 has NEVER successfully recorded a")
    print("      cycle, or state was reset. Check whether the scheduled task")
    print("      is actually running (Task Scheduler -> look for a task that")
    print("      runs run_madeonsol_hidden.vbs periodically).")
print()

print("=" * 70)
print(f"2) Your roster has {len(SELL_WATCH_ROSTER)} tracked names:")
print("   " + ", ".join(sorted(SELL_WATCH_ROSTER)))
print()

print("=" * 70)
print("3) Last 100 raw alerts from fomoapi.io /v2/alerts (no cursor filter):")
result = fetch_fomo_alerts(since_iso=None, limit=100)
if not result["ok"]:
    print(f"   FAILED: {result.get('reason')}")
else:
    alerts = result["alerts"]
    print(f"   {len(alerts)} alert(s) returned")
    if not alerts:
        print("   -> fomoapi.io itself returned nothing. That's the real gap --")
        print("      nothing to match against, roster or not.")
    matched = 0
    unmatched_traders = set()
    for a in alerts:
        alert_type = a.get("alertType")
        trader = a.get("trader")
        hit = _match_roster(trader)
        if hit:
            matched += 1
            print(f"   [{alert_type:>8}] trader={trader!r:30} -> MATCHED: {hit}")
        elif alert_type in ("buy", "thesis"):
            unmatched_traders.add(trader)
    print()
    print(f"   {matched} alert(s) matched your roster out of {len(alerts)} total.")
    if unmatched_traders:
        print(f"   {len(unmatched_traders)} distinct buy/thesis trader handle(s) did NOT match:")
        for t in sorted(unmatched_traders):
            print(f"     - {t!r}")
        print("   If any of these are names you recognize from your Following")
        print("   list, that confirms a spelling/casing mismatch against")
        print("   roster.py -- tell me the exact handle and I'll fix it there.")
