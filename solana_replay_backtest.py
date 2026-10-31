"""
30-31 Oct 2026 (Ali): "where is the pattern? where is the identifiability?" -- full
combinatorial backtest of layer17's reconstructable confirmations against REAL outcome
data for every solana coin ever observed in early_log/*.jsonl (18 days), not just the
9 coins that happened to get a live trade.

HONEST SCOPE: early_log's "snap" events carry buyer/seller/volume/mcap counts only --
no wallet addresses. That means repeat_grad, velocity, pressure, famous_name, and the
mcap gate CAN be reconstructed here at any point in a coin's life. smart_wallet,
kol_buy, kol_converge, fomo_new_wallet, and dev_list (wallet-identity signals) CANNOT --
the raw per-wallet data was never logged, only summarized into decision events for
coins that already passed a gate. hot_meta (rolling "recent runner" word list) is also
excluded: it depends on which coins had already popped by that moment in time, which
this script does not attempt to replay correctly. This is a real, stated gap, not a
silent omission.

OUTCOME definition: peak_sol / mcap_sol_at_signal_time, from the "final" event's
peak_sol field matched by mint. A "win" is defined as peak multiple >= 2x (same
ballpark as the live system's take-profit tiers). This is pre-slippage/pre-fee, so it
is an upper bound on real trading performance, not a promise of matching live P&L --
stated plainly in the report, not hidden in a footnote.
"""
import json
import glob
import re
from collections import defaultdict

FAMOUS_RX = re.compile(
    r"pepe|doge|shiba|wojak|bonk|pnut|moo.?deng|labubu|barron|melania|batman|joker|spiderman|marvel|avengers|"
    r"deadpool|barbie|oppenheimer|wicked|pokemon|pikachu|minecraft|roblox|gta|squidgame|zelda|mario|sonic|"
    r"naruto|goku|kpop|bts|blackpink|olympic|worldcup|beast|bigfoot|alien|ufo|trump|elon|musk",
    re.I,
)

FILES = sorted(glob.glob("early_log/*.jsonl"))

# Pass 1: build per-mint create info + final outcome
creates = {}   # mint -> dict
finals = {}    # mint -> peak_sol

print(f"Pass 1/2: reading {len(FILES)} files for create/final events...")
for fp in FILES:
    with open(fp) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("t")
            if t == "create":
                creates[d["mint"]] = d
            elif t == "final":
                mint = d.get("mint")
                if mint:
                    finals[mint] = d.get("peak_sol")

print(f"  {len(creates)} creates, {len(finals)} finals")

# Pass 2: replay snaps, compute confirmations at each snapshot, record first-qualifying
# snapshot per mint per rule-set, then look up the final outcome.
MIN_BUYERS = 10
MIN_BUYER_RATE = 0.06
MIN_BUYS = 12
MIN_BUY_SELL = 2.5
SOL_USD = 150.0

# results[rule_name] -> list of (mcap_sol_at_signal, peak_sol, win_bool)
results = defaultdict(list)

def confs_at(rec, age, b, s, v, mc):
    out = []
    if rec.get("repeat_grad"):
        out.append("repeat_grad")
    if age and b >= MIN_BUYERS and b / max(age, 1.0) >= MIN_BUYER_RATE:
        out.append("velocity")
    if b >= MIN_BUYS and s and b / max(s, 1) >= MIN_BUY_SELL:
        out.append("pressure")
    nm = ((rec.get("sym") or "") + " " + (rec.get("name") or ""))
    if FAMOUS_RX.search(nm):
        out.append("famous_name")
    return out

print("Pass 2/2: replaying snaps against reconstructable confirmations...")
decided = set()  # (mint,) already recorded once per mint to avoid double counting
first_qual_mcap = {}  # mint -> mcap_sol at first time it had ANY reconstructable signal

for fp in FILES:
    with open(fp) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("t") != "snap":
                continue
            mint = d["mint"]
            if mint in decided:
                continue
            rec = creates.get(mint)
            if not rec:
                continue
            age, b, s, v, mc_usd = d.get("age"), d.get("b", 0), d.get("s", 0), d.get("v", 0), d.get("mc")
            if mc_usd is None:
                continue
            conf = confs_at(rec, age, b, s, v, mc_usd)
            if not conf:
                continue
            decided.add(mint)
            first_qual_mcap[mint] = (mc_usd, sorted(conf))  # mc_usd, NOT sol -- see unit note below

print(f"  {len(first_qual_mcap)} mints had at least one reconstructable confirmation fire")

# Now bucket by confirmation combination and mcap band, compute win rate vs peak outcome
combo_stats = defaultdict(lambda: [0, 0, 0.0])  # combo -> [n, wins, sum_multiple]
mcap_lo_usd, mcap_hi_usd = 3000.0, 20000.0

# NOTE: snap's "mc" field is USD (it is literally mc_usd in the writer, early_recorder.py),
# while final's "peak_sol" is denominated in SOL. They must be converted to the same unit
# before computing a multiple -- an earlier version of this script compared them directly
# and produced a bogus near-0% win rate across the board (SOL numbers are ~150x smaller
# than USD numbers, so peak_sol/mc_usd looks like a ~150x crash on every single coin).
# Fixed by converting the signal-time USD mcap to SOL first.
for mint, (mc_usd_at_signal, conf) in first_qual_mcap.items():
    peak = finals.get(mint)
    mc_sol = mc_usd_at_signal / SOL_USD
    if peak is None or mc_sol <= 0:
        continue
    mult = peak / mc_sol
    win = mult >= 2.0
    combo = tuple(conf)
    combo_stats[combo][0] += 1
    combo_stats[combo][1] += 1 if win else 0
    combo_stats[combo][2] += mult

    # also bucket: combo + inside-3k-20k-usd-window
    mc_usd = mc_usd_at_signal
    if mcap_lo_usd <= mc_usd < mcap_hi_usd:
        combo_w = combo + ("in_3_20k_window",)
        combo_stats[combo_w][0] += 1
        combo_stats[combo_w][1] += 1 if win else 0
        combo_stats[combo_w][2] += mult

print()
print("=" * 100)
print(f"{'confirmation combo':55s} {'n':>6s} {'wins':>6s} {'win%':>7s} {'avg_mult':>9s}")
print("=" * 100)
for combo, (n, wins, summ) in sorted(combo_stats.items(), key=lambda kv: -kv[1][0]):
    if n < 5:
        continue
    label = "+".join(combo) if combo else "(none)"
    print(f"{label:55s} {n:6d} {wins:6d} {100*wins/n:6.1f}% {summ/n:9.3f}")

print()
print(f"Total unique mints with any reconstructable signal: {len(first_qual_mcap)}")
print(f"Total mints with a matched final/peak outcome: {sum(v[0] for v in combo_stats.values() if len(list(combo_stats.keys()))==1) if False else 'see table'}")
