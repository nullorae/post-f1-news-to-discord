import json
import os
import urllib.request
import xml.etree.ElementTree as ET

# Replace this with an RSS or Atom feed URL from an F1 news publisher
FEED_URL = "https://f1tv-rss.vercel.app"

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "state.json"


def text_of(element, tag):
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_id": ""}

    with open(STATE_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def fetch_latest_item():
    request = urllib.request.Request(
        FEED_URL,
        headers={"User-Agent": "F1-Discord-News-Bot/1.0"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)

    # RSS feed support
    item = root.find("./channel/item")
    if item is not None:
        title = text_of(item, "title")
        link = text_of(item, "link")
        item_id = text_of(item, "guid") or link or title
        return item_id, title, link

    # Atom feed support
    atom_namespace = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", atom_namespace)
    if entry is not None:
        title = text_of(entry, "{http://www.w3.org/2005/Atom}title")
        item_id = text_of(entry, "{http://www.w3.org/2005/Atom}id") or title

        link_element = entry.find("atom:link", atom_namespace)
        link = link_element.attrib.get("href", "") if link_element is not None else ""

        return item_id, title, link

    raise RuntimeError("No RSS item or Atom entry was found in the feed.")


def post_to_discord(title, link):
    payload = {
        "username": "F1 News",
        "content": f"🏎️ **{title}**\n{link}",
        "allowed_mentions": {"parse": []},
    }

    request = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Discord returned HTTP {response.status}")


def main():
    state = load_state()
    item_id, title, link = fetch_latest_item()

    if item_id == state.get("last_id"):
        print("No new article.")
        return

    post_to_discord(title, link)
    save_state({"last_id": item_id})
    print(f"Posted: {title}")


if __name__ == "__main__":
    main()
