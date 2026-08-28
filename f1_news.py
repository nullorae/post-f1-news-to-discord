import json
import os
import sys
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# F1 news Atom feed.
FEED_URL = "https://f1tv-rss.vercel.app/api/rss"
STATE_FILE = "state.json"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# Seconds to wait between Discord posts to respect rate limits.
POST_DELAY_SECONDS = 1

# Discord webhook URL, read once at module level. Exit early if missing so the
# failure is obvious rather than surfacing as a NameError deep in the code.
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not WEBHOOK_URL:
    print(
        "Error: DISCORD_WEBHOOK_URL environment variable is not set.",
        file=sys.stderr,
    )
    sys.exit(1)


def fetch_feed_items():
    """Fetch and parse the Atom feed, returning a list of entries.

    Each entry is a dict with keys: id, title, link. Entries are returned in
    feed order (newest first, as the feed publishes them).
    """
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
    for entry in root.findall("atom:entry", ATOM_NS):
        title_elem = entry.find("atom:title", ATOM_NS)
        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""

        id_elem = entry.find("atom:id", ATOM_NS)
        entry_id = id_elem.text.strip() if id_elem is not None and id_elem.text else ""

        link = ""
        for link_elem in entry.findall("atom:link", ATOM_NS):
            if link_elem.attrib.get("rel") in (None, "alternate"):
                link = link_elem.attrib.get("href", "")
                break

        # Fall back to link/title when the feed omits an explicit <id>.
        entry_id = entry_id or link or title

        if not entry_id:
            continue

        items.append({"id": entry_id, "title": title, "link": link})

    return items


# Cap on how many posted IDs we retain, to keep state.json from growing forever.
MAX_POSTED_IDS = 200


def load_state():
    """Load persisted state. Returns a dict with a 'posted_ids' list.

    Handles migration from the older {"last_id": "X"} format: the previous
    marker is seeded into posted_ids so the existing backlog is not re-posted.
    """
    if not os.path.exists(STATE_FILE):
        return {"posted_ids": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
        if not isinstance(state, dict):
            return {"posted_ids": []}

        # New format.
        if isinstance(state.get("posted_ids"), list):
            return {"posted_ids": [str(x) for x in state["posted_ids"] if x]}

        # Migrate old single-marker format.
        last_id = state.get("last_id")
        if last_id:
            return {"posted_ids": [str(last_id)]}

        return {"posted_ids": []}
    except Exception:
        return {"posted_ids": []}


def save_state(state):
    # Keep only the most recent IDs to bound file growth.
    posted = state.get("posted_ids", [])
    state["posted_ids"] = posted[-MAX_POSTED_IDS:]
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def post_to_discord(title, link):
    """Post a single item to Discord. Returns True on success, False otherwise."""
    payload = {"content": f"**{title}**\n{link}"}

    request = urllib.request.Request(
        WEBHOOK_URL,
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
                return False
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Discord HTTPError {exc.code}: {body}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Failed to post to Discord: {exc}", file=sys.stderr)
        return False

    return True


def main():
    state = load_state()
    posted_set = set(state.get("posted_ids", []))

    feed_items = fetch_feed_items()
    if not feed_items:
        print("No items found in the feed.")
        return

    # The feed is newest-first and contains duplicate entries (same id repeated).
    # Collapse to unique ids while preserving feed order, then keep only the
    # ones we have never posted before. Tracking a set (rather than a single
    # marker) makes us resilient to duplicates and any reordering in the feed.
    seen = set()
    new_items = []
    for item in feed_items:
        item_id = item["id"]
        if item_id in seen or item_id in posted_set:
            continue
        seen.add(item_id)
        new_items.append(item)

    if not new_items:
        print("No new items since the last run.")
        return

    # Post oldest-first so Discord shows items in chronological order.
    new_items.reverse()

    for index, item in enumerate(new_items):
        if post_to_discord(item["title"], item["link"]):
            state["posted_ids"].append(item["id"])
            print(f"Successfully posted: {item['title']}")
        else:
            print(f"Skipping remaining items after failure on: {item['title']}", file=sys.stderr)
            break

        # Sleep between posts (but not after the final one).
        if index < len(new_items) - 1:
            time.sleep(POST_DELAY_SECONDS)

    save_state(state)


if __name__ == "__main__":
    main()
