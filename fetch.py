#!/usr/bin/env python3
"""Pull YouTube's most-popular chart for several countries and save a daily snapshot.

Needs the env var YT_API_KEY. Costs about 1 quota unit per 50 videos
(8 countries x 4 pages = 32 units a run; the free limit is 10,000 a day).
Writes data/latest.json, data/YYYY-MM-DD.json and data/index.json.
"""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://www.googleapis.com/youtube/v3/videos"
REGIONS = ["GB", "US", "IE", "CA", "AU", "IN", "NG", "ZA"]  # GB first: home market
PAGES_PER_REGION = 4      # 4 x 50 = 200 videos, the most this chart gives
SHORT_MAX_SECONDS = 180   # Shorts can run up to 3 min, so this is an estimate
KEEP_DAYS = 30            # daily files older than this are deleted
DATA = Path("data")
KEY = os.environ.get("YT_API_KEY", "").strip()


class ApiError(Exception):
    pass


def get(params, tries=4):
    url = API + "?" + urllib.parse.urlencode({**params, "key": KEY})
    err = ""
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (400, 401, 403, 404):  # bad key, quota or region: retrying won't help
                raise ApiError(f"HTTP {e.code}: {body}")
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            err = str(e)
        if attempt < tries:
            print(f"   retry {attempt}/{tries - 1} in {2 ** attempt}s ({err})")
            time.sleep(2 ** attempt)
    raise ApiError(f"gave up after {tries} tries ({err})")


def seconds(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def fetch_region(region):
    items, token = [], None
    for _ in range(PAGES_PER_REGION):
        params = {"part": "snippet,statistics,contentDetails", "chart": "mostPopular",
                  "regionCode": region, "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = get(params)
        items += data.get("items", [])
        token = data.get("nextPageToken")
        if not token:
            break
    return items


def slim(v, now):
    sn, st, cd = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
    pub = sn.get("publishedAt")
    views = int(st.get("viewCount", 0) or 0)
    hours = None
    if pub:
        age = now - datetime.fromisoformat(pub.replace("Z", "+00:00"))
        hours = max(age.total_seconds() / 3600, 1)
    dur = seconds(cd.get("duration"))
    return {
        "id": v["id"],
        "title": sn.get("title", ""),
        "channel": sn.get("channelTitle", ""),
        "channelId": sn.get("channelId"),
        "category": sn.get("categoryId"),
        "published": pub,
        "duration": dur,
        "short": dur is not None and 0 < dur <= SHORT_MAX_SECONDS,
        "views": views,
        "likes": int(st.get("likeCount", 0) or 0),
        "comments": int(st.get("commentCount", 0) or 0),
        "viewsPerHour": round(views / hours) if hours else None,
        "tags": (sn.get("tags") or [])[:8],
        "thumb": sn.get("thumbnails", {}).get("medium", {}).get("url"),
        "ranks": {},
    }


def main():
    if not KEY:
        sys.exit("YT_API_KEY is not set")
    now = datetime.now(timezone.utc)
    DATA.mkdir(exist_ok=True)

    prev_views = {}
    latest = DATA / "latest.json"
    if latest.exists():
        try:
            prev_views = {v["id"]: v["views"] for v in json.loads(latest.read_text())["videos"]}
        except (ValueError, KeyError):
            pass

    videos, done, failed = {}, [], []
    for region in REGIONS:  # one country at a time, so one failure doesn't sink the run
        print(f"{region}: fetching...")
        try:
            items = fetch_region(region)
        except ApiError as e:
            print(f"{region}: skipped, {e}")
            failed.append(region)
            continue
        for rank, v in enumerate(items, 1):
            rec = videos.get(v["id"]) or slim(v, now)
            rec["ranks"].setdefault(region, rank)  # keep the best rank if a video repeats
            videos[v["id"]] = rec
        done.append(region)
        print(f"{region}: {len(items)} videos")

    if not done:
        sys.exit("Every country failed, nothing saved")

    for rec in videos.values():
        rec["regionCount"] = len(rec["ranks"])
        old = prev_views.get(rec["id"])
        rec["gain"] = rec["views"] - old if old is not None else None  # views since last run

    rows = sorted(videos.values(), key=lambda r: (-r["regionCount"], min(r["ranks"].values())))
    snap = {"fetchedAt": now.isoformat(timespec="seconds"), "regions": done,
            "failed": failed, "videos": rows}
    day = now.strftime("%Y-%m-%d")
    text = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    latest.write_text(text)
    (DATA / f"{day}.json").write_text(text)

    days = sorted((p.stem for p in DATA.glob("20??-??-??.json")), reverse=True)
    for old_day in days[KEEP_DAYS:]:
        (DATA / f"{old_day}.json").unlink()
    (DATA / "index.json").write_text(json.dumps({"days": days[:KEEP_DAYS]}))

    print(f"\nSaved {len(rows)} videos from {len(done)} countries ({', '.join(done)})")
    if failed:
        print(f"Skipped: {', '.join(failed)}")
    uk = sorted((r for r in rows if "GB" in r["ranks"]), key=lambda r: r["ranks"]["GB"])[:5]
    print("\nTop 5 UK:")
    for r in uk:
        print(f"  {r['ranks']['GB']:>3}. {r['title'][:70]}")
    print("\nIn the most countries:")
    for r in rows[:5]:
        print(f"  {r['regionCount']} countries  {r['title'][:65]}")


if __name__ == "__main__":
    main()
