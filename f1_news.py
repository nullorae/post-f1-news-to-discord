import json
import os
import sys
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# Replace this with an RSS or Atom feed URL from an F1 news publisher
FEED_URL = "https://f1tv-rss.vercel.app/api/rss"
STATE_FILE = "state.json"

# Prevent a large backlog from flooding Discord on the first run.
MAX_POSTS_PER_RUN = 5
POST_DELAY_SECONDS = 1


def text_of(element, tag):
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen_ids": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        if "seen_ids" not in state:
            last_id = state.get("last_id", "")
            state = {"seen_ids": [last_id] if last_id else []}

        return state
    except Exception:
        return {"seen_ids": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch_feed_items():
    request = urllib.request.Request(
        FEED_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; F1DiscordBot/1.0)"},
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            xml_data = response.read()
    except Exception as exc:
        print(f"Failed to fetch feed from {FEED_URL}: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        root = ET.fromstring(xml_data)
    except Exception as exc:
        print(f"Failed to parse XML: {exc}", file=sys.stderr)
        sys.exit(1)

    items = []
    unique_ids = set()

    # Try RSS 2.0 <channel><item> first.
    rss_items = root.findall("./channel/item")

    if rss_items:
        for item in rss_items:
            title = text_of(item, "title")
            link = text_of(item, "link")
            item_id = text_of(item, "guid") or link or title

            if not item_id or item_id in unique_ids:
                continue

            unique_ids.add(item_id)
            items.append({"id": item_id, "title": title, "link": link})
    else:
        # Fall back to Atom <feed><entry>.
        for entry in root.findall("atom:entry", ATOM_NS):
            title_elem = entry.find("atom:title", ATOM_NS)
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""

            id_elem = entry.find("atom:id", ATOM_NS)
            item_id = id_elem.text.strip() if id_elem is not None and id_elem.text else ""

            link = ""
            for link_elem in entry.findall("atom:link", ATOM_NS):
                if link_elem.attrib.get("rel") in (None, "alternate"):
                    link = link_elem.attrib.get("href", "")
                    break

            item_id = item_id or link or title

            if not item_id or item_id in unique_ids:
                continue

            unique_ids.add(item_id)
            items.append({"id": item_id, "title": title, "link": link})

    if not items:
        print("Error: No valid RSS items or Atom entries found.", file=sys.stderr)
        sys.exit(1)

    return items


def post_to_discord(title, link):
    payload = {
        "username": "F1 News",
        "content": f"🏎️ **{title}**\n{link}",
        "allowed_mentions": {"parse": []},
    }

    request = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (github.com/nullorae/post-f1-news-to-discord, 1.0)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 204):
                print(f"Discord returned HTTP status {response.status}", file=sys.stderr)
                sys.exit(1)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Discord HTTPError {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)


def main():
    state = load_state()
    seen_ids = set(state.get("seen_ids", []))

    feed_items = fetch_feed_items()

    # RSS feeds normally place newest posts first. Reverse the unseen posts
    # so Discord receives them oldest to newest.
    new_items = [item for item in reversed(feed_items) if item["id"] not in seen_ids]
    items_to_post = new_items[:MAX_POSTS_PER_RUN]

    if not items_to_post:
        print("No new items since the last run.")
        return

    for item in items_to_post:
        post_to_discord(item["title"], item["link"])
        seen_ids.add(item["id"])
        print(f"Successfully posted: {item['title']}")
        time.sleep(POST_DELAY_SECONDS)

    # Retain a bounded history so state.json does not grow forever.
    state["seen_ids"] = list(seen_ids)[-500:]
    save_state(state)

if __name__ == "__main__":
    main()
