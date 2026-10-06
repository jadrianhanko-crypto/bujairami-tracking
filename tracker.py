#!/usr/bin/env python3
"""
Bujairami tracker for fragrancenet.com

Alerts you (phone push via ntfy.sh, optional Discord) when:
  1. a NEW Bujairami fragrance shows up on the brand page, or
  2. a fragrance that was out of stock comes BACK in stock.

First run just records a baseline and sends nothing.

Setup:  pip install requests
Run:    NTFY_TOPIC=your-secret-topic python tracker.py
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

BASE = "https://www.fragrancenet.com"
# Brand listing pages to scan for product links. Add more if the brand page is paginated.
LISTING_URLS = [
    f"{BASE}/fn/fragrances/bujairami",
    f"{BASE}/fn/search/Bujairami",
]
STATE_FILE = Path(__file__).with_name("state.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
DELAY = 2  # seconds between requests, keep it polite

# product URLs look like /fn/cologne/bujairami/bujairami-hectic/extrait-de-parfum
PRODUCT_RE = re.compile(r'href="([^"#?]*?/bujairami/(bujairami-[a-z0-9\-]+)/([a-z0-9\-]+))"', re.I)
OUT_MARKERS = re.compile(r"out of stock|sold out|notify me|currently unavailable|back in stock", re.I)


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code != 200 or len(r.text) < 2000:
        raise RuntimeError(f"Bad response {r.status_code} ({len(r.text)} bytes) for {url}")
    return r.text


def find_products():
    """Return {key: product_url} for every Bujairami product linked from the listing pages."""
    found = {}
    for url in LISTING_URLS:
        try:
            html = get(url)
        except Exception as e:  # one listing failing shouldn't kill the run
            print(f"warn: {e}", file=sys.stderr)
            continue
        print(f"DEBUG {url}: {len(html)} bytes, 'bujairami' x{html.lower().count('bujairami')}, starts: {html[:150]!r}")
        for href, slug, kind in PRODUCT_RE.findall(html):
            if "/es/" in href:  # skip Spanish-language duplicates
                continue
            key = f"{slug}/{kind}"
            found.setdefault(key, href if href.startswith("http") else BASE + href)
        time.sleep(DELAY)
    return found


def in_stock(url):
    """Heuristic: an in-stock page shows 'our price' and none of the out-of-stock markers."""
    html = get(url)
    has_price = re.search(r"our price", html, re.I) is not None
    # 'back in stock' also appears inside customer reviews, so only trust it outside review text
    markers = [m for m in OUT_MARKERS.findall(html) if m.lower() != "back in stock"]
    return has_price and not markers


def notify(title, message, url=None):
    print(f"ALERT: {title} | {message}")
    if NTFY_TOPIC:
        headers = {"Title": title, "Priority": "high", "Tags": "perfume"}
        if url:
            headers["Click"] = url
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode(), headers=headers, timeout=15)
    if DISCORD_WEBHOOK:
        requests.post(DISCORD_WEBHOOK, json={"content": f"**{title}**\n{message}\n{url or ''}"}, timeout=15)


def pretty(key):
    return key.split("/")[0].replace("-", " ").title() + " (" + key.split("/")[1].replace("-", " ") + ")"


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    first_run = not state

    products = find_products()
    if not products:
        print("No products found (page blocked or layout changed). State untouched.", file=sys.stderr)
        sys.exit(1)

    new_state = dict(state)  # keep products that temporarily vanish from the listing
    for key, url in products.items():
        try:
            stock = in_stock(url)
        except Exception as e:
            print(f"warn: could not check {key}: {e}", file=sys.stderr)
            time.sleep(DELAY)
            continue
        prev = state.get(key)
        if not first_run:
            if prev is None:
                notify("New Bujairami listed", f"{pretty(key)} just appeared on FragranceNet "
                       f"({'in stock' if stock else 'currently out of stock'}).", url)
            elif prev["in_stock"] is False and stock:
                notify("Bujairami back in stock", f"{pretty(key)} is available again.", url)
        new_state[key] = {"in_stock": stock, "url": url}
        time.sleep(DELAY)

    STATE_FILE.write_text(json.dumps(new_state, indent=2, sort_keys=True))
    print(f"Checked {len(products)} products. {'Baseline saved.' if first_run else 'Done.'}")


if __name__ == "__main__":
    main()
