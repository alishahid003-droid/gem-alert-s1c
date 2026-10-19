"""catalyst_report.py - reads catalyst_log/*.jsonl and prints, per verdict (HEADS-UP / CONFIRMED / LATE) and
trusted-source vs not, how often the coin was higher after 15m / 1h / 6h / 24h and the median multiple.
Run:  python catalyst_report.py     (read-only; writes catalyst_log/report.txt)"""
import glob, json, os, statistics
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalyst_log")
alerts, outs = {}, []
for fp in sorted(glob.glob(os.path.join(D, "*.jsonl"))):
    for line in open(fp, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("t") == "alert":
            alerts[(r["mint"], r["ts"])] = r
        elif r.get("t") == "outcome":
            outs.append(r)
lines = [f"alerts logged: {len(alerts)}   outcome rows: {len(outs)}"]
by = {}
for o in outs:
    by.setdefault((o["verdict"], o["horizon_min"]), []).append(o["mult"])
for v in ("HEADS-UP", "CONFIRMED", "LATE"):
    n = sum(1 for a in alerts.values() if a["verdict"] == v)
    lines.append(f"\n{v}: {n} alerts")
    for h in (15, 60, 360, 1440):
        m = by.get((v, h), [])
        if m:
            lines.append(f"  +{h:>4}m  n={len(m):>3}  up {100*sum(x>1 for x in m)/len(m):4.0f}%  median x{statistics.median(m):.2f}  "
                         f"best x{max(m):.2f}  >=2x {100*sum(x>=2 for x in m)/len(m):3.0f}%  <=0.5x {100*sum(x<=0.5 for x in m)/len(m):3.0f}%")
tr = [a for a in alerts.values() if a.get("trusted")]
lines.append(f"\nalerts from trusted sources: {len(tr)} of {len(alerts)}")
lines.append("Read: an edge exists only if median multiple at +1h/+6h clearly beats ~0.85 (our round-trip cost) AND >=2x share is meaningful.")
txt = "\n".join(lines); print(txt)
os.makedirs(D, exist_ok=True); open(os.path.join(D, "report.txt"), "w", encoding="utf-8").write(txt)
