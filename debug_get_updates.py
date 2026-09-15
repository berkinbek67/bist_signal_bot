"""
One-off debug script: prints the raw getUpdates response from your bot.
Use this to find a group's chat ID -- add the bot to the group, send it
a message that mentions the bot (e.g. "@your_bot_username hi") or a
command (e.g. "/id"), then run this script and look for the group's
chat id in the output (a negative number, e.g. -1001234567890).

Mentioning/commanding the bot guarantees Telegram delivers the message
even if the bot's group privacy mode is still on -- no admin promotion
or settings changes needed.
"""

import os
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
response = requests.get(url, timeout=10)
data = response.json()

print("Full response:")
print(data)
print()

updates = data.get("result", [])
if not updates:
    print("No updates found. Make sure you sent a message that mentions the bot or a command, then run this again.")
else:
    print("Chats seen:")
    seen = set()
    for u in updates:
        msg = u.get("message", {})
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        chat_title = chat.get("title") or chat.get("username") or "(private chat)"
        if chat_id not in seen:
            seen.add(chat_id)
            print(f"  id={chat_id}  type={chat_type}  name={chat_title}")
