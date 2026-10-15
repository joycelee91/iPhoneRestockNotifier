#!/usr/bin/env python3
"""Apple Store Malaysia pickup-availability notifier -> Telegram.

Polls Apple's fulfillment-messages endpoint and sends a Telegram message
when a part becomes available for pickup at a store whose name matches
STORE_KEYWORD (default "TRX").

Usage:
  python3 notifier.py --test          # send a test Telegram message
  python3 notifier.py --list          # show every store + availability now
  python3 notifier.py                 # run forever, polling
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- config (override with env vars) ---------------------------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# Comma-separated Apple part numbers, e.g. "MXXXXPA/A". Find them on the
# product page (see README.md).
PARTS = [p.strip() for p in os.environ.get("APPLE_PARTS", "").split(",") if p.strip()]
STORE_KEYWORD = os.environ.get("STORE_KEYWORD", "TRX").lower()
LOCATION = os.environ.get("APPLE_LOCATION", "Kuala Lumpur")
INTERVAL = int(os.environ.get("POLL_SECONDS", "60"))
BASE = "https://www.apple.com/my/shop"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept": "application/json",
}


def fetch_stores():
    """Return stores near LOCATION. Apple MY only exposes the pickup
    'recommendations' endpoint: a requested part appears under a store's
    partsAvailability only when it can be picked up there; otherwise Apple
    lists other models instead."""
    q = [("mts.0", "regular"), ("location", LOCATION), ("product", PARTS[0])]
    q += [(f"parts.{i}", p) for i, p in enumerate(PARTS)]
    url = f"{BASE}/pickup-message-recommendations?{urllib.parse.urlencode(q, safe='/')}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    return data["body"]["PickupMessage"]["stores"]


def telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=20) as r:
        r.read()


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def check_once(state):
    stores = [s for s in fetch_stores() if STORE_KEYWORD in s.get("storeName", "").lower()]
    if not stores:
        log(f"no store matching '{STORE_KEYWORD}' returned")
    for store in stores:
        parts = store.get("partsAvailability", {})
        for part in PARTS:
            info = parts.get(part)
            avail = bool(info) and info.get("pickupDisplay") == "available"
            title = (info or {}).get("messageTypes", {}).get("regular", {}).get(
                "storePickupProductTitle", part)
            key = (store["storeNumber"], part)
            log(f"{store['storeName']} | {title} | {'AVAILABLE' if avail else 'unavailable'}")
            if avail and not state.get(key):
                telegram(f"🟢 In stock for pickup at Apple {store['storeName']}!\n"
                         f"{title}\n"
                         f"Order now: https://www.apple.com/my/shop/buy-iphone/iphone-18-pro")
            state[key] = avail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--once", action="store_true", help="check once and exit (for cron/CI)")
    args = ap.parse_args()

    if args.test or not args.list:
        if not BOT_TOKEN or not CHAT_ID:
            sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
    if args.test:
        telegram("✅ Apple restock notifier is connected.")
        return log("Test message sent.")
    if not PARTS:
        sys.exit("Set APPLE_PARTS to the part number(s) to watch.")
    if args.list:
        for st in fetch_stores():
            have = st.get("partsAvailability", {})
            for part in PARTS:
                ok = have.get(part, {}).get("pickupDisplay") == "available"
                print(f"{st['storeName']:<24} {part}  {'available' if ok else 'unavailable'}")
        return

    if args.once:
        # State lives in a file so alerts fire only on unavailable -> available.
        path = os.environ.get("STATE_FILE", "state.json")
        try:
            with open(path) as f:
                state = {tuple(k.split("|", 1)): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            state = {}
        check_once(state)
        with open(path, "w") as f:
            json.dump({"|".join(k): v for k, v in state.items()}, f)
        return

    state, fails = {}, 0
    log(f"Watching {PARTS} at stores matching '{STORE_KEYWORD}' every {INTERVAL}s")
    while True:
        try:
            check_once(state)
            fails = 0
        except Exception as e:  # network blips, Apple rate limits, etc.
            fails += 1
            log(f"error: {e}")
            if fails == 10:
                try:
                    telegram(f"⚠️ Notifier failing repeatedly: {e}")
                except Exception:
                    pass
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
