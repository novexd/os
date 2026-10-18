#!/usr/bin/env python3
"""Independent RSS -> Nerimity polling worker. Python 3.10+, stdlib only."""
import json
import logging
import os
import re
import signal
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

def load_local_env():
    """Load local secrets without requiring python-dotenv or overriding panel variables."""
    for path in (Path(__file__).resolve().parent / ".env.local", Path(__file__).resolve().parent / ".env"):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)


load_local_env()

LOG = logging.getLogger("rss_worker")
WEBHOOK = os.environ.get("NERIMITY_WEBHOOK", "")
STATE = Path(os.environ.get("RSS_STATE_FILE", str(Path(__file__).with_name("last_seen.json"))))
REDDIT_INTERVAL = max(60, int(os.environ.get("REDDIT_INTERVAL", "120")))
X_INTERVAL = max(60, int(os.environ.get("X_INTERVAL", "300")))
STOP = False
FEEDS = {
    "x_osugame": {
        "url": "https://keep.md/api/x-rss/osugame.xml?content=posts",
        "label": "@osugame on X", "username": "osu! News",
        "avatar": "https://pbs.twimg.com/profile_images/706741102120800256/xOf4lxBy_400x400.jpg",
        "interval": X_INTERVAL,
    },
    "x_osu_SOTD": {
        "url": "https://keep.md/api/x-rss/osu_SOTD.xml?content=posts",
        "label": "@osu_SOTD on X", "username": "osu! SOTD",
        "avatar": "https://pbs.twimg.com/profile_images/1278305103963963393/Y6qHo5ZT_400x400.png",
        "interval": X_INTERVAL,
    },
    "reddit_osugame": {
        "url": "https://www.reddit.com/r/osugame/new/.rss?limit=100",
        "label": "r/osugame", "username": "r/osugame",
        "avatar": "https://i.redd.it/n13pg82qpjd41.png",
        "interval": REDDIT_INTERVAL,
    },
}


def stop_handler(*_):
    global STOP
    STOP = True


def request(url, payload=None):
    headers = {"User-Agent": "OsuFeedGrabberBot/1.0 (independent RSS worker)",
               "Accept": "application/atom+xml, application/rss+xml, application/xml, application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    data = json.dumps(payload).encode() if payload is not None else None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            LOG.warning("HTTP %s from %s; retrying", exc.code, url.split("?")[0])
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            LOG.warning("Connection issue; retrying")
        time.sleep(2 ** (attempt + 1))


def load_state():
    if not STATE.exists():
        return {}
    return json.loads(STATE.read_text(encoding="utf-8"))


def save_state(history):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_suffix(STATE.suffix + ".tmp")
    temp.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(STATE)


def entries(xml):
    root = ET.fromstring(xml)
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == atom + "feed":
        result = []
        for entry in root.findall(atom + "entry"):
            guid = entry.findtext(atom + "id", default="")
            title = entry.findtext(atom + "title", default="New post")
            link = next((e.get("href", "") for e in entry.findall(atom + "link")
                         if e.get("rel", "alternate") == "alternate"), "")
            if guid and link:
                result.append((guid, title, link))
        return result
    result = []
    for entry in root.findall("./channel/item"):
        link = entry.findtext("link", default="")
        guid = entry.findtext("guid") or link
        if guid and link:
            result.append((guid, entry.findtext("title", default="New post"), link))
    return result


def looks_like_gameplay(title):
    t = title.casefold()
    if re.search(r"\b\d[\d,.]*\s?pp\b", t):
        return True
    stats = (r"\b\d{2,3}(?:\.\d+)?\s?%", r"\b\d[\d,]*\s?(?:x|combo)\b",
             r"\b(?:fc|ss|s rank|a rank|first pass|new top play|personal best|world record)\b",
             r"(?:#1|rank\s?#\d+)")
    context = (r"\b(?:osu!|std|mania|taiko|catch|ctb|score|play|clear|pass|sightread|"
               r"replay|leaderboard|map|beatmap|pp|accuracy|acc|combo|fc|dt|hd|hr|ez|fl)\b"
               r"|\[[^\]]+\]")
    return any(re.search(p, t) for p in stats) and bool(re.search(context, t))


def send(name, feed, title, link):
    message = f"**New post from {feed['label']}**\n{title}\n\n{link}"
    request(WEBHOOK, {
        "content": f"[r:1829648046925979648] {message}" if name != "reddit_osugame" else message,
        "username": feed["username"], "avatarUrl": feed["avatar"],
        "silent": name == "reddit_osugame",
    })


def check(name, feed, history):
    posts = entries(request(feed["url"]))
    if not posts:
        LOG.info("%s: empty feed", name)
        return
    previous = history.get(name)
    if not previous:
        history[name] = posts[0][0]
        save_state(history)
        LOG.info("%s: initialized checkpoint without sending old posts", name)
        return
    unseen = []
    for post in posts:
        if post[0] == previous:
            break
        unseen.append(post)
    else:
        # If the checkpoint disappeared from the feed, don't replay its entire history.
        history[name] = posts[0][0]
        save_state(history)
        LOG.warning("%s: checkpoint not found in feed; reseeded without backfill", name)
        return
    sent = 0
    for guid, title, link in reversed(unseen):
        if name != "reddit_osugame" or looks_like_gameplay(title):
            send(name, feed, title, link)
            sent += 1
        history[name] = guid
        save_state(history)
    LOG.info("%s: %d new items, %d sent", name, len(unseen), sent)


def main():
    if not WEBHOOK:
        raise SystemExit("Missing NERIMITY_WEBHOOK environment variable")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    history = load_state()
    # Monotonic deadlines avoid clock-change problems.
    deadlines = {name: 0.0 for name in FEEDS}
    LOG.info("Started independent RSS worker; state=%s", STATE)
    while not STOP:
        now = time.monotonic()
        for name, feed in FEEDS.items():
            if STOP:
                break
            if now < deadlines[name]:
                continue
            deadlines[name] = time.monotonic() + feed["interval"]
            try:
                check(name, feed, history)
            except Exception:
                LOG.exception("%s: poll failed; other feeds will continue", name)
            now = time.monotonic()
        if not STOP:
            time.sleep(min(5, max(0.2, min(deadlines.values()) - time.monotonic())))
    LOG.info("Worker stopped")


if __name__ == "__main__":
    main()
