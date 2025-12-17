from telethon import TelegramClient, events
from datetime import datetime, timedelta
import asyncio

API_ID = 39858841
API_HASH = 'de06619decf663b5ef5cba304cb04d5e'
PHONE = '+380684214577'

client = TelegramClient('my_session', API_ID, API_HASH)
last_online_time = {}

@client.on(events.NewMessage(incoming=True))
async def auto_reply_handler(event):
    if not event.is_private or not event.message.text or event.message.out:
        return
    
    sender_id = event.sender_id
    current_time = datetime.now()
    
    if sender_id in last_online_time:
        time_diff = current_time - last_online_time[sender_id]
        if time_diff < timedelta(minutes=1):
            return
    
    print(f"⏰ Отримано від {sender_id}, чекаю 1 хвилину...")
    await asyncio.sleep(60)
    
    reply_text = "Зараз зайнятий/на. Відпишу пізніше ✌️"
    
    try:
        await client.send_message(
            sender_id,
            reply_text,
            reply_to=event.message.id
        )
        print(f"✅ Відповів {sender_id}")
        last_online_time[sender_id] = datetime.now()
    except Exception as e:
        print(f"❌ Помилка: {e}")

async def main():
    print("🔐 Авторизація...")
    await client.start(phone=PHONE)
    
    me = await client.get_me()
    print(f"✅ Увійшов як: {me.first_name}")
    print("🤖 Бот запущено! Відповідатиму через 1 хвилину після повідомлення.")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот зупинено")
