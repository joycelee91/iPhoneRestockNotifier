# Apple MY restock notifier (TRX pickup -> Telegram)

## 1. Telegram bot
1. In Telegram, message **@BotFather** -> `/newbot` -> copy the **token**.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id":...}`.

## 2. Part number
Open the iPhone 18 Pro Max buy page on apple.com/my, pick model + 256GB + colour.
Part number looks like `MXXXXPA/A` (shown in the page URL/ "Part number" in
the bag, or in the page's source as `"partNumber"`). Add one per colour you'd accept.

## 3. Run
```bash
export TELEGRAM_BOT_TOKEN="123:abc"
export TELEGRAM_CHAT_ID="123456789"
export APPLE_PARTS="MXXXXPA/A,MYYYYPA/A"

python3 notifier.py --test    # Telegram connectivity
python3 notifier.py --list    # confirm TRX appears for your part
python3 notifier.py           # watch (default every 60s)
```
Keep it running (Mac awake, or put it on a cheap VPS / Raspberry Pi).
Optional env: `POLL_SECONDS`, `STORE_KEYWORD` (default `TRX`), `APPLE_LOCATION`.

You get a message only when stock flips from unavailable -> available.
