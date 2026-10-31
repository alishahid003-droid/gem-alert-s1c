import json, glob, re
from collections import defaultdict

FAMOUS_RX = re.compile(
    r"pepe|doge|shiba|wojak|bonk|pnut|moo.?deng|labubu|barron|melania|batman|joker|spiderman|marvel|avengers|"
    r"deadpool|barbie|oppenheimer|wicked|pokemon|pikachu|minecraft|roblox|gta|squidgame|zelda|mario|sonic|"
    r"naruto|goku|kpop|bts|blackpink|olympic|worldcup|beast|bigfoot|alien|ufo|trump|elon|musk",
    re.I,
)
FILES = sorted(glob.glob("early_log/*.jsonl"))
creates, finals = {}, {}
for fp in FILES:
    with open(fp) as f:
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get("t")
            if t == "create": creates[d["mint"]] = d
            elif t == "final":
                m = d.get("mint")
                if m: finals[m] = d.get("peak_sol")

MIN_BUYERS, MIN_BUYER_RATE, MIN_BUYS, MIN_BUY_SELL, SOL_USD = 10, 0.06, 12, 2.5, 150.0

def confs_at(rec, age, b, s, mc_usd):
    out = []
    if rec.get("repeat_grad"): out.append("repeat_grad")
    if age and b >= MIN_BUYERS and b / max(age, 1.0) >= MIN_BUYER_RATE: out.append("velocity")
    if b >= MIN_BUYS and s and b / max(s, 1) >= MIN_BUY_SELL: out.append("pressure")
    nm = ((rec.get("sym") or "") + " " + (rec.get("name") or ""))
    if FAMOUS_RX.search(nm): out.append("famous_name")
    return out

decided = set()
first_qual = {}
for fp in FILES:
    with open(fp) as f:
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            if d.get("t") != "snap": continue
            mint = d["mint"]
            if mint in decided: continue
            rec = creates.get(mint)
            if not rec: continue
            age, b, s, mc_usd = d.get("age"), d.get("b", 0), d.get("s", 0), d.get("mc")
            if mc_usd is None: continue
            conf = confs_at(rec, age, b, s, mc_usd)
            if not conf: continue
            decided.add(mint)
            first_qual[mint] = (mc_usd, sorted(conf))

with open("/tmp/first_qual_cache.json", "w") as f:
    json.dump({"first_qual": first_qual, "finals": finals}, f)
print("cached", len(first_qual), "mints")
