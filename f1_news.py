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


def load_state():
    """Load persisted state. Returns a dict containing a 'last_id' key."""
    if not os.path.exists(STATE_FILE):
        return {"last_id": ""}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
        if not isinstance(state, dict):
            return {"last_id": ""}
        state.setdefault("last_id", "")
        return state
    except Exception:
        return {"last_id": ""}


def save_state(state):
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
    last_id = state.get("last_id", "")

    feed_items = fetch_feed_items()
    if not feed_items:
        print("No items found in the feed.")
        return

    # The feed lists newest entries first. Walk from newest to oldest and stop
    # once we reach the last item we already posted. Everything before that
    # marker (i.e. already posted) is discarded.
    new_items = []
    for item in feed_items:
        if item["id"] == last_id:
            break
        new_items.append(item)

    if not new_items:
        print("No new items since the last run.")
        return

    # Post oldest-first so Discord shows items in chronological order.
    new_items.reverse()

    posted_ids = []
    for index, item in enumerate(new_items):
        if post_to_discord(item["title"], item["link"]):
            posted_ids.append(item["id"])
            print(f"Successfully posted: {item['title']}")
        else:
            print(f"Skipping remaining items after failure on: {item['title']}", file=sys.stderr)
            break

        # Sleep between posts (but not after the final one).
        if index < len(new_items) - 1:
            time.sleep(POST_DELAY_SECONDS)

    if posted_ids:
        # Newest successfully posted item becomes the new marker.
        state["last_id"] = posted_ids[-1]
        save_state(state)


if __name__ == "__main__":
    main()
