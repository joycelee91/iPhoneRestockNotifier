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
import re
import sys
import threading
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
NO_STOCK_STICKER = os.environ.get("TELEGRAM_NO_STOCK_STICKER", "")
IN_STOCK_STICKER = os.environ.get("TELEGRAM_IN_STOCK_STICKER", "")
BASE = "https://www.apple.com/my/shop"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept": "application/json",
}


def fetch_stores(parts=None):
    """Return stores near LOCATION with per-part pickup availability.
    Apple caps each response at ~20 parts, so request in chunks and merge."""
    parts = parts or PARTS
    stores = {}
    for k in range(0, len(parts), 10):
        q = [("pl", "true"), ("location", LOCATION)]
        q += [(f"parts.{i}", p) for i, p in enumerate(parts[k:k + 10])]
        url = f"{BASE}/retail/pickup-message?{urllib.parse.urlencode(q, safe='/')}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        for st in data["body"]["stores"]:
            merged = stores.setdefault(st["storeNumber"], {**st, "partsAvailability": {}})
            merged["partsAvailability"].update(st.get("partsAvailability", {}))
    return list(stores.values())


def telegram_api(method, params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    body = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=20) as r:
        data = json.load(r)
    if not data.get("ok"):
        raise RuntimeError(data.get("description", f"Telegram {method} failed"))
    return data.get("result")


def telegram(text, chat_id=CHAT_ID):
    telegram_api("sendMessage", {"chat_id": chat_id, "text": text})


def telegram_sticker(sticker, chat_id=CHAT_ID):
    if sticker:
        telegram_api("sendSticker", {"chat_id": chat_id, "sticker": sticker})


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def check_once(state, send_transition_alerts=True, update_state=True):
    stores = [s for s in fetch_stores() if STORE_KEYWORD in s.get("storeName", "").lower()]
    if not stores:
        log(f"no store matching '{STORE_KEYWORD}' returned")
    results = []
    for store in stores:
        parts = store.get("partsAvailability", {})
        for part in PARTS:
            info = parts.get(part)
            avail = bool(info) and info.get("pickupDisplay") == "available"
            title = (info or {}).get("messageTypes", {}).get("regular", {}).get(
                "storePickupProductTitle", part)
            key = (store["storeNumber"], part)
            log(f"{store['storeName']} | {title} | {'AVAILABLE' if avail else 'unavailable'}")
            results.append((store["storeName"], part, title, avail))
            if send_transition_alerts and avail and not state.get(key):
                telegram(f"🟢 In stock for pickup at Apple {store['storeName']}!\n"
                         f"{title}\n"
                         f"Order now: https://www.apple.com/my/shop/buy-iphone/iphone-18-pro")
            if update_state:
                state[key] = avail
    return results


# ---- product catalog (model / storage / colour -> part number) --------------
CATALOG_PAGES = os.environ.get("CATALOG_PAGES", "iphone-18-pro").split(",")
_catalog = {"at": 0, "items": []}


def load_catalog():
    """Scrape Apple's buy pages for every iPhone 18 variant (cached 1 hour)."""
    if _catalog["items"] and time.time() - _catalog["at"] < 3600:
        return _catalog["items"]
    items = {}
    for page in CATALOG_PAGES:
        req = urllib.request.Request(f"{BASE}/buy-iphone/{page.strip()}", headers={**HEADERS, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
        for part, name in re.findall(
                r'"partNumber":"([A-Z0-9]+/A)","price":\{[^}]*\},"category":"iphone","name":"([^"]+)"', html):
            name = " ".join(name.split())  # Apple uses non-breaking spaces
            m = re.match(r"(iPhone .+?) (\d+(?:GB|TB)) (.+)$", name)
            if m:
                items[part] = {"part": part, "model": m[1], "storage": m[2], "colour": m[3]}
    if items:
        _catalog.update(at=time.time(), items=list(items.values()))
    return _catalog["items"]


def _size(storage):
    return int(storage[:-2]) * (1024 if storage.endswith("TB") else 1)


def options(items, key):
    vals = {i[key] for i in items}
    return sorted(vals, key=_size) if key == "storage" else sorted(vals)


def select(model="*", storage="*", colour="*"):
    return [i for i in load_catalog()
            if model in ("*", i["model"]) and storage in ("*", i["storage"])
            and colour in ("*", i["colour"])]


def stock_report(items):
    """Check pickup stock for the given catalog items; return (text, any_available)."""
    if not items:
        return "No matching iPhone found.", False
    stores = [s for s in fetch_stores([i["part"] for i in items])
              if STORE_KEYWORD in s.get("storeName", "").lower()]
    if not stores:
        return f"No store matching '{STORE_KEYWORD}' returned.", False
    lines, any_ok = [], False
    for store in stores:
        have = store.get("partsAvailability", {})
        lines.append(f"📍 Apple {store['storeName']}")
        for i in sorted(items, key=lambda i: (i["model"], _size(i["storage"]), i["colour"])):
            ok = have.get(i["part"], {}).get("pickupDisplay") == "available"
            any_ok |= ok
            lines.append(f"{'✅' if ok else '❌'} {i['model']} {i['storage']} {i['colour']}")
    return "\n".join(lines), any_ok


def send_result(chat_id, items):
    try:
        text, any_ok = stock_report(items)
        try:
            telegram_sticker(IN_STOCK_STICKER if any_ok else NO_STOCK_STICKER, chat_id)
        except (RuntimeError, urllib.error.URLError) as e:
            log(f"could not send sticker: {e}")
        head = "YESSSSS IT'S IN 🎉🎉🎉" if any_ok else "No stock yet 😢😭 I checked just now."
        telegram(f"{head}\n\n{text}", chat_id)
    except Exception as e:
        log(f"manual check error: {e}")
        telegram(f"⚠️ I couldn't check stock right now: {e}", chat_id)


def keyboard(rows):
    """rows: list of rows of (label, callback_data)."""
    return json.dumps({"inline_keyboard": [
        [{"text": t, "callback_data": d} for t, d in row] for row in rows]})


def menu(step, model="*", storage="*"):
    """Return (prompt, keyboard) for the next /checkstock step."""
    def grid(pairs, per=2):
        return [pairs[i:i + per] for i in range(0, len(pairs), per)]
    if step == "model":
        opts = [(m, f"m|{m}") for m in options(load_catalog(), "model")]
        return "Which model?", keyboard(grid(opts) + [[("All models", "m|*")]])
    if step == "storage":
        opts = [(s, f"s|{model}|{s}") for s in options(select(model), "storage")]
        return f"{model if model != '*' else 'All models'}: which storage?", keyboard(
            grid(opts, 3) + [[("Any storage", f"s|{model}|*")]])
    opts = [(c, f"c|{model}|{storage}|{c}") for c in options(select(model, storage), "colour")]
    label = " ".join(x for x in (model if model != "*" else "", storage if storage != "*" else "") if x)
    return f"{label or 'All models'}: which colour?", keyboard(
        grid(opts) + [[("Any colour", f"c|{model}|{storage}|*")]])


def parse_text_query(args):
    """'/checkstock pro max 256gb black' -> (model, storage, colour), '*' = any."""
    text = " ".join(args).lower()
    items = load_catalog()
    words = set(re.findall(r"[a-z0-9]+", text))
    fits = [m for m in options(items, "model")
            if (tags := m.lower().split()[2:]) and all(t in words for t in tags)]
    model = max(fits, key=len) if fits else "*"
    storage = next((s for s in options(items, "storage") if s.lower() in text.replace(" ", "")), "*")
    colour = next((c for c in options(items, "colour") if c.lower() in text), "*")
    return model, storage, colour


def handle_command(chat_id, args):
    if args:
        model, storage, colour = parse_text_query(args)
        return send_result(chat_id, select(model, storage, colour))
    prompt, kb = menu("model")
    telegram_api("sendMessage", {"chat_id": chat_id, "text": prompt, "reply_markup": kb})


def handle_callback(cb):
    chat_id = str(cb["message"]["chat"]["id"])
    msg_id = cb["message"]["message_id"]
    telegram_api("answerCallbackQuery", {"callback_query_id": cb["id"]})
    kind, *rest = cb["data"].split("|")

    def edit(prompt, kb=None):
        p = {"chat_id": chat_id, "message_id": msg_id, "text": prompt}
        if kb:
            p["reply_markup"] = kb
        telegram_api("editMessageText", p)

    if kind == "m":
        edit(*menu("storage", rest[0]))
    elif kind == "s":
        edit(*menu("colour", rest[0], rest[1]))
    elif kind == "c":
        model, storage, colour = rest
        desc = " ".join(x for x in (model, storage, colour) if x != "*") or "all iPhone 18 models"
        edit(f"Checking {desc}…")
        send_result(chat_id, select(model, storage, colour))


def telegram_listener(state, state_lock):
    """Listen for commands while the automatic polling loop continues."""
    offset = 0
    while True:
        try:
            updates = telegram_api("getUpdates", {
                "offset": offset,
                "timeout": min(INTERVAL, 30),
                "allowed_updates": json.dumps(["message", "callback_query"]),
            }) or []
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    cb = update.get("callback_query")
                    if cb:
                        if str(cb["message"]["chat"]["id"]) == str(CHAT_ID):
                            handle_callback(cb)
                        continue
                    message = update.get("message", {})
                    if str(message.get("chat", {}).get("id", "")) != str(CHAT_ID):
                        continue
                    words = message.get("text", "").strip().split()
                    if words and words[0].lower().split("@", 1)[0] == "/checkstock":
                        handle_command(str(CHAT_ID), words[1:])
                except Exception as e:  # one bad update shouldn't stop the listener
                    log(f"command error: {e}")
        except Exception as e:
            log(f"Telegram command listener error: {e}")
            time.sleep(5)


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
    state_lock = threading.Lock()
    listener = threading.Thread(
        target=telegram_listener, args=(state, state_lock), daemon=True
    )
    listener.start()
    log(f"Watching {PARTS} at stores matching '{STORE_KEYWORD}' every {INTERVAL}s")
    while True:
        try:
            with state_lock:
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
