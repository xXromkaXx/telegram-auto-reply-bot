from telethon import TelegramClient, events
from telethon.tl.types import UserStatusOnline, UserStatusOffline
from telethon.tl.functions.account import UpdateStatusRequest
from datetime import datetime, timedelta
from telethon.sessions import StringSession
import asyncio
import os

import re

API_ID = 39858841
API_HASH = 'de06619decf663b5ef5cba304cb04d5e'


SESSION_STRING = os.getenv("SESSION_STRING")

# ===== СТАНИ =====
last_reply_time = {}
blocked_chats = set()   # чати, де бот вже відповів
is_online = False
me = None

GREETINGS = re.compile(r'\b(привіт|вітаю|hello|hi|hey|ку)\b', re.IGNORECASE)
client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)

# ===== Перевірка: чи новий чат (у чаті ще НЕМА твоїх повідомлень)
async def is_new_chat(chat_id):
    async for _ in client.iter_messages(chat_id, from_user='me', limit=1):
        return False
    return True


# ===== ONLINE / OFFLINE статус
@client.on(events.UserUpdate)
async def user_status_handler(event):
    global is_online

    if event.user_id != me.id:
        return

    if isinstance(event.status, UserStatusOnline):
        is_online = True
        print("🟢 ONLINE — бот мовчить")

    elif isinstance(event.status, UserStatusOffline):
        is_online = False
        print("🔴 OFFLINE — бот активний")


# ===== Якщо ТИ сам написав — розблоковуємо чат
@client.on(events.NewMessage(outgoing=True))
async def my_message_handler(event):
    if event.is_private:
        chat_id = event.chat_id
        if chat_id in blocked_chats:
            blocked_chats.remove(chat_id)
            print(f"🔓 Чат {chat_id} розблоковано (ти написав)")


# ===== Автовідповіді
@client.on(events.NewMessage(incoming=True))
async def auto_reply_handler(event):
    if not event.is_private or not event.text or event.out:
        return

    if is_online:
        return

    chat_id = event.chat_id
    sender_id = event.sender_id
    text = event.text.lower()
    now = datetime.now()

    # ❌ Бот уже відповідав у цьому чаті
    if chat_id in blocked_chats:
        return

    # ⏱ Антиспам (1 хв)
    if sender_id in last_reply_time:
        if now - last_reply_time[sender_id] < timedelta(minutes=1):
            return

    # 🆕 ЛОГІКА НОВОГО ЧАТУ
    if await is_new_chat(chat_id):
        reply_text = (
            "Привіт! 👋\n"
            "Зараз мене немає онлайн, але я обов’язково відповім трохи пізніше 😊"
        )
    else:
        if GREETINGS.search(text):
            reply_text = "Привіт! 👋 Зараз зайнятий, відпишу пізніше ✌️"
        else:
            reply_text = "Зараз зайнятий, відпишу пізніше ✌️"

    print(f"⏰ Відповідаю {sender_id} через 1 хв...")
    await asyncio.sleep(60)

    try:
        await client.send_message(
            sender_id,
            reply_text,
            reply_to=event.message.id
        )

        # 🔥 Повертаємо OFFLINE (Telegram сам робить ONLINE на мить)
        await client(UpdateStatusRequest(offline=True))

        last_reply_time[sender_id] = datetime.now()
        blocked_chats.add(chat_id)

        print(f"✅ Відповів і повернув OFFLINE (чат {chat_id})")

    except Exception as e:
        print(f"❌ Помилка: {e}")


# ===== MAIN
async def main():
    global me

    await client.start() 
    me = await client.get_me()

    print(f"✅ Увійшов як: {me.first_name}")
    print("🤖 AFK-бот активний")
    print("📌 1 відповідь → мовчить → твій меседж → знов активний")

    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот зупинено")
