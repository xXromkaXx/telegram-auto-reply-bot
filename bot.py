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
scheduled_messages = {}  # заплановані повідомлення: {chat_id: task}

GREETINGS = re.compile(r'\b(привіт|вітаю|hello|hi|hey|ку|доброго дня|день добрий|добрий вечір)\b', re.IGNORECASE)
DAIVINCHIK = re.compile(r'\b(дайвінчик|Дайвінчика)\b', re.IGNORECASE)

client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)

# ===== Перевірка: чи є в чаті мої повідомлення (вся історія)
async def has_my_messages(chat_id):
    """
    Перевіряє, чи є в цьому чаті ХОЧА Б ОДНЕ моє повідомлення (навіть старше)
    Повертає True, якщо я колись писав у цей чат
    """
    try:
        # Шукаємо останні 100 повідомлень в чаті від мене
        async for message in client.iter_messages(chat_id, limit=100, from_user='me'):
            if message.out:  # Якщо це моє повідомлення
                return True
    except Exception as e:
        print(f"⚠️ Помилка перевірки чату {chat_id}: {e}")
    
    # Також перевіримо через get_messages для надійності
    try:
        messages = await client.get_messages(chat_id, limit=10, from_user='me')
        if messages:
            print(f"✅ В чаті {chat_id} знайдено {len(messages)} моїх повідомлень")
            return True
    except Exception as e:
        print(f"⚠️ Помилка get_messages для чату {chat_id}: {e}")
    
    print(f"❌ В чаті {chat_id} не знайдено моїх повідомлень")
    return False


# ===== ONLINE / OFFLINE статус
@client.on(events.UserUpdate)
async def user_status_handler(event):
    global is_online

    if event.user_id != me.id:
        return

    if isinstance(event.status, UserStatusOnline):
        is_online = True
        print("🟢 ONLINE — бот мовчить і скасовує заплановані повідомлення")
        
        # Скасовуємо всі заплановані повідомлення
        for chat_id, task in list(scheduled_messages.items()):
            if not task.done():
                task.cancel()
                print(f"❌ Скасовано заплановане повідомлення для чату {chat_id}")
        
        # Очищаємо словник
        scheduled_messages.clear()

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
# Скасовуємо заплановане повідомлення для цього чату (якщо є)
        if chat_id in scheduled_messages:
            task = scheduled_messages[chat_id]
            if not task.done():
                task.cancel()
                print(f"❌ Скасовано заплановане повідомлення для чату {chat_id} (ти написав)")
            del scheduled_messages[chat_id]

# ===== Автовідповіді
@client.on(events.NewMessage(incoming=True))
async def auto_reply_handler(event):
    if not event.is_private or not event.text or event.out:
        return

    if is_online:
        return

    chat_id = event.chat_id
    sender_id = event.sender_id
    text = event.text
    text_lower = text.lower()
    now = datetime.now()

    # ❌ Бот уже відповідав у цьому чаті
    if chat_id in blocked_chats:
        return

    # ⏱ Антиспам (1 хв)
    if sender_id in last_reply_time:
        if now - last_reply_time[sender_id] < timedelta(minutes=1):
            return

    # ===== ОСНОВНА ЛОГІКА =====
    # Перевірка: чи я колись писав у цей чат (вся історія)
    i_wrote_before = await has_my_messages(chat_id)
    
    print(f"🔍 Чат {chat_id}: я писав раніше = {i_wrote_before}")
    
    # Якщо НОВИЙ чат (я ніколи не писав туди)
    if not i_wrote_before:
        # Якщо є слово "дайвінчик" - спеціальна відповідь
        if DAIVINCHIK.search(text):
            reply_text = "Привіт! Бачу ти з дайвінчика 😊 ромка зараз відпочиває або дуже зайнятий, але скоро буде з тобою!"
        else:
            # Стандартна відповідь для нового чату
            reply_text = "Привіт! Я зараз зайнятий, надіюсь не срочне повідомлення. Відповім як зможу!"
    
    else:  # Чат вже існуючий (я колись в ньому писав)
        # Якщо повідомлення містить вітання
        if GREETINGS.search(text):
            reply_text = "Привіт! Зараз зайнятий, відпишу пізніше ✌️"
        # Інакше - без вітання
        else:
            reply_text = "Зараз зайнятий, відпишу пізніше ✌️"

    print(f"⏰ Відповідаю {sender_id} через 1 хв...")
    # Створюємо асинхронну задачу для відправки через 1 хвилину
    async def send_delayed_message():
        try:
            # Перевіряємо кожні 5 секунд, чи не став я онлайн
            for i in range(12):  # 12 * 5 секунд = 60 секунд
                await asyncio.sleep(5)
                if is_online:
                    print(f"🚫 Скасовано відправку для чату {chat_id} (я став ONLINE)")
                    if chat_id in scheduled_messages:
                        del scheduled_messages[chat_id]
                    return
            
            # Перевіряємо ще раз перед відправкою
            if is_online:
                print(f"🚫 Не відправляю повідомлення в чат {chat_id} (я ONLINE)")
                return
                
            # Перевіряємо, чи чат все ще не заблокований
            if chat_id in blocked_chats:
                print(f"🚫 Чат {chat_id} вже заблокований")
                return
            
            print(f"📤 Надсилаю заплановане повідомлення для {sender_id}")
            
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
        
            
        except asyncio.CancelledError:
            print(f"❌ Задача для чату {chat_id} скасована")
        except Exception as e:
            print(f"❌ Помилка при відправці в чат {chat_id}: {e}")
        finally:
            # Видаляємо задачу зі словника
            if chat_id in scheduled_messages:
                del scheduled_messages[chat_id]
    
    # Створюємо і зберігаємо задачу
    task = asyncio.create_task(send_delayed_message())
    scheduled_messages[chat_id] = task


# ===== MAIN
async def main():
    global me

    await client.start() 
    me = await client.get_me()

    print(f"✅ Увійшов як: {me.first_name}")
    print("🤖 AFK-бот активний")

    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот зупинено")
