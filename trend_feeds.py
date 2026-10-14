"""Free trend feeds -> hot terms for the early watcher (14 Oct 2026). No keys, no cost.

Sources (all public): Google Trends daily-trending RSS (16 countries), Reddit r/all + r/popular hot titles,
Wikipedia most-viewed articles (yesterday), Bluesky + Mastodon trending, X (Twitter) trends via the trends24 mirror,
TikTok Creative Center hashtags (best effort: TikTok may refuse). Instagram/Facebook have no public trend feed. Every cycle the merged terms (with source and first-seen) are written to the state key `trend_terms_v1`;
the early watcher reads it next to the old Radar's `radar_hot_terms`.

A coin whose name/ticker matches a trending term gets the `narrative` confirmation; the FIRST coin ever to match a
trending term gets `narrative_first` as well (see layer17). Tuning is done from logged data, not guessed."""
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (compatible; gem-alert/1.0)"
GEOS = ("US", "GB", "IN", "PK", "CA", "AU", "NG", "BR", "DE", "FR", "JP", "KR", "MX", "PH", "ID", "TR")
STOP = set("""the and for with that this from have has had not are was were you your his her its our out all new
one two can will just now get got how why what when who which about into over after before more most than then them they
their there here also been being very much some any other only like game video official live news today best top first last
world people man woman time year years day days week show season movie film part full full trailer vs""".split())
MIN_LEN = 4


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:     # noqa: S310 (public https feeds only)
        return r.read().decode("utf-8", "ignore")


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def parse_google_rss(xml_text):
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        t = it.find("title")
        if t is not None and t.text:
            out.append(t.text.strip())
    return out


def parse_reddit(json_text):
    import json
    try:
        kids = json.loads(json_text)["data"]["children"]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for k in kids:
        title = (k.get("data") or {}).get("title") or ""
        # proper-noun style names: "John Phil", "Moo Deng", "Elon Musk"
        out += re.findall(r"\b([A-Z][a-z]{2,}(?: [A-Z][a-z]{2,}){1,2})\b", title)
    return out


def parse_wiki_top(json_text):
    import json
    try:
        arts = json.loads(json_text)["items"][0]["articles"]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in arts[:80]:
        t = (a.get("article") or "").replace("_", " ")
        if not t or t.startswith(("Main Page", "Special:", "Wikipedia:", "Portal:", "File:", "Category:", "Help:")):
            continue
        out.append(t)
    return out


def _hashtag(t):
    t = re.sub(r"^#", "", (t or "").strip())
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", t)          # #MooDeng -> Moo Deng


def parse_bluesky(json_text):
    import json
    try:
        d = json.loads(json_text)
    except Exception:  # noqa: BLE001
        return []
    return [_hashtag(t.get("topic") or t.get("displayName") or "") for t in (d.get("topics") or []) if isinstance(t, dict)]


def parse_mastodon_tags(json_text):
    import json
    try:
        d = json.loads(json_text)
    except Exception:  # noqa: BLE001
        return []
    return [_hashtag(t.get("name") or "") for t in d if isinstance(t, dict)]


def parse_trends24(html):
    """X (Twitter) trends via the public trends24 mirror."""
    import html as _h
    names = re.findall(r'class="trend-link"[^>]*>([^<]{2,60})<', html or "")
    return [_hashtag(_h.unescape(n)) for n in names[:60]]


def parse_tiktok(json_text):
    import json
    try:
        items = json.loads(json_text)["data"]["list"]
    except Exception:  # noqa: BLE001
        return []
    return [_hashtag(i.get("hashtag_name") or "") for i in items if isinstance(i, dict)]


def terms_from_titles(titles):
    """-> {term: kind}. Full phrase (spaced), squashed phrase, and distinctive single words (>=4 chars, not stop words)."""
    out = {}
    for raw in titles:
        p = norm(raw)
        if not p or len(p) < 3:
            continue
        words = p.split()
        if len(words) >= 2:
            out[p] = "phrase"
            out[p.replace(" ", "")] = "phrase"
        elif len(p) >= MIN_LEN and p not in STOP:
            out[p] = "word"
        for w in words:
            if len(w) >= MIN_LEN and w not in STOP and not w.isdigit():
                out.setdefault(w, "word")
    return out


def fetch_all(now=None):
    """-> (titles_by_source dict, notes list). Never raises."""
    now = now or time.time()
    srcs, notes = {}, []
    g = []
    for geo in GEOS:
        try:
            g += parse_google_rss(_get(f"https://trends.google.com/trending/rss?geo={geo}"))
        except Exception as e:  # noqa: BLE001
            notes.append(f"gtrends {geo}: {type(e).__name__}")
    srcs["google"] = g
    r = []
    for sub in ("all", "popular"):
        try:
            r += parse_reddit(_get(f"https://www.reddit.com/r/{sub}/hot.json?limit=100"))
        except Exception as e:  # noqa: BLE001
            notes.append(f"reddit {sub}: {type(e).__name__}")
    srcs["reddit"] = r
    def _many(name, urls, parse, headers=None):
        got = []
        for u in urls:
            try:
                got += parse(_get(u))
            except Exception as e:  # noqa: BLE001
                notes.append(f"{name}: {type(e).__name__}")
        srcs[name] = got
    _many("bluesky", ["https://public.api.bsky.app/xrpc/app.bsky.unspecced.getTrendingTopics?limit=25"], parse_bluesky)
    _many("mastodon", [f"https://{h}/api/v1/trends/tags?limit=20" for h in ("mastodon.social", "mstdn.social")], parse_mastodon_tags)
    _many("x", [f"https://trends24.in/{c}/" for c in ("united-states", "united-kingdom", "india", "pakistan", "nigeria", "brazil")], parse_trends24)
    _many("tiktok", [f"https://ads.tiktok.com/creative_radar_api/v1/popular_trend/hashtag/list?page=1&limit=50&period=7&country_code={c}&sort_by=popular"
                     for c in ("US", "GB", "PK")], parse_tiktok)
    try:
        y = time.gmtime(now - 86400)
        srcs["wiki"] = parse_wiki_top(_get(
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{y.tm_year}/{y.tm_mon:02d}/{y.tm_mday:02d}"))
    except Exception as e:  # noqa: BLE001
        srcs["wiki"] = []
        notes.append(f"wiki: {type(e).__name__}")
    return srcs, notes


def refresh(state, now=None, fetch=fetch_all):
    """Fetch, merge with what we already knew (first-seen kept), write both state keys. Returns (n_terms, notes)."""
    now = now or time.time()
    srcs, notes = fetch(now)
    book = state.get_value("trend_terms_v1")
    book = book if isinstance(book, dict) else {}
    seen_now = set()
    for src, titles in srcs.items():
        for term, kind in terms_from_titles(titles).items():
            seen_now.add(term)
            rec = book.get(term)
            if not rec:
                book[term] = {"first": now, "last": now, "src": [src], "kind": kind}
            else:
                rec["last"] = now
                if src not in rec["src"]:
                    rec["src"].append(src)
    for t in [t for t, rec in book.items() if now - float(rec.get("last") or 0) > 48 * 3600]:
        del book[t]                                              # forget terms not trending for 2 days
    state.set_value("trend_terms_v1", book)
    return len(book), notes
