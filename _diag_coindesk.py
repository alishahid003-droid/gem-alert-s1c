from layers.layer4_news import fetch_coindesk_rss, parse_coindesk_rss

r = fetch_coindesk_rss()
raw = r["raw"]
print("ok:", r["ok"], "status_code:", raw.get("status_code"))
text = raw.get("text") or ""
print("full text length:", len(text))
posts = parse_coindesk_rss(text)
print("parsed posts:", len(posts))
for p in posts[:10]:
    print("-", p["title"][:70], "| currencies:", p["currencies"])
