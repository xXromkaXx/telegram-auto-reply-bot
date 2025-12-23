from telethon import TelegramClient, events
from telethon.tl.types import UserStatusOnline, UserStatusOffline
from telethon.tl.functions.account import UpdateStatusRequest
from datetime import datetime, timedelta
from telethon.sessions import StringSession
import asyncio
import os
import re
from openai import OpenAI

API_ID = 39858841
API_HASH = 'de06619decf663b5ef5cba304cb04d5e'
SESSION_STRING = os.getenv("SESSION_STRING")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client_ai = OpenAI(api_key=OPENAI_API_KEY)

# Словник для зберігання історії діалогів: chat_id -> список повідомлень
chat_histories = {}
# Словник для зберігання завдань на "акумуляцію" повідомлень: chat_id -> asyncio.Task
message_accumulator_tasks = {}
# Словник для зберігання буфера повідомлень для акумуляції
message_buffers = {}
# Словник для часу останньої активності в чаті
last_activity_time = {}

ABOUT_ME = """
Мене звати Роман, мені 18 років.
Я нормальний простий хлопець не курю.
Люблю спорт, програмування, гуляти, живе спілкування.
Пишу просто, тепло, без пафосу.
Спілкуюсь українською, іноді з емодзі.
"""
MEET_QUESTION = re.compile(
    r'(познайом|знайом|не проти|можна|давай знайом)',
    re.IGNORECASE
)

# ===== СТАНИ =====
last_reply_time = {}
blocked_chats = set()   # чати, де бот вже відповів
is_online = False
me = None
scheduled_messages = {}  # заплановані повідомлення: {chat_id: task}
offline_since = None  # час, коли ти пішов в офлайн

GREETINGS = re.compile(r'\b(привіт|вітаю|hello|hi|hey|ку|доброго дня|день добрий|добрий вечір)\b', re.IGNORECASE)
DAIVINCHIK = re.compile(r'\b(дайвінчик|Дайвінчика)\b', re.IGNORECASE)

client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)

async def generate_gpt_reply(chat_history, force_meet=False):
    """
    Генерує відповідь на основі всієї історії діалогу в чаті.
    chat_history: список словників [{"role": "user", "content": "текст"}, ...]
    """
    instruction = "Відповідай природно на повідомлення людини. Пам'ятай, що це діалог у чаті, де можуть бути кілька повідомлень підряд. Розумій контекст попередніх повідомлень."
    if force_meet:
        instruction = "Перше повідомлення. Ти м'яко і приємно ініціюєш знайомство."

    # Формуємо системне повідомлення з інструкцією та фактами
    system_message = {
        "role": "system",
        "content": f"""Ти — це я, реальний хлопець 18 років. Спілкуєшся в дайвінчику.
Пиши по-людськи, тепло, без офіційності. Факти про мене: {ABOUT_ME}
Інструкція: {instruction}
Відповідай розгорнуто, але природно, як у реальному спілкуванні."""
    }

    # Об'єднуємо системне повідомлення з історією діалогу
    messages_for_gpt = [system_message] + chat_history[-20:]  # Беремо останні 20 повідомлень для контексту

    try:
        response = client_ai.chat.completions.create(
            model="gpt-4o-mini",  # або інша доступна модель
            messages=messages_for_gpt,
            max_tokens=300,
            temperature=0.85,
            presence_penalty=0.1,
            frequency_penalty=0.1
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Помилка GPT: {e}")
        return "Зараз зайнятий, відпишу пізніше ✌️"

async def process_accumulated_messages(chat_id):
    """
    Обробляє накопичені повідомлення для чату та відправляє одну відповідь
    """
    # Даємо трохи часу на завершення акумуляції
    await asyncio.sleep(0.5)
    
    # Отримуємо всі накопичені повідомлення
    if chat_id not in message_buffers:
        return
    
    messages = message_buffers[chat_id]
    if not messages:
        message_buffers.pop(chat_id, None)
        message_accumulator_tasks.pop(chat_id, None)
        return
    
    # Додаємо повідомлення до історії чату
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    
    # Додаємо всі накопичені повідомлення до історії
    for msg in messages:
        chat_histories[chat_id].append({"role": "user", "content": msg})
    
    print(f"📝 Обробляю {len(messages)} накопичених повідомлень для чату {chat_id}")
    
    # Перевіряємо, чи є у історії повідомлення з ключовим словом "дайвінчик"
    force_meet = False
    all_messages_text = " ".join([msg.lower() for msg in messages])
    if DAIVINCHIK.search(all_messages_text):
        force_meet = True
        print(f"🎯 Виявлено 'дайвінчик' в накопичених повідомленнях, активую режим знайомства")
    
    # Генеруємо відповідь на основі всієї історії
    reply_text = await generate_gpt_reply(chat_histories[chat_id], force_meet)
    
    try:
        # Відправляємо відповідь
        await client.send_message(chat_id, reply_text)
        print(f"✅ Відправили відповідь GPT у чат {chat_id}")
        
        # Додаємо відповідь до історії
        chat_histories[chat_id].append({"role": "assistant", "content": reply_text})
        
        # Оновлюємо статус та очищуємо завдання
        await client(UpdateStatusRequest(offline=True))
        blocked_chats.add(chat_id)
        
        # Не очищуємо історію, щоб зберігати контекст для наступного спілкування
        # Зберігаємо лише останні 30 повідомлень для економії пам'яті
        if len(chat_histories[chat_id]) > 30:
            chat_histories[chat_id] = chat_histories[chat_id][-30:]
            
    except Exception as e:
        print(f"❌ Помилка при відправці відповіді: {e}")
    finally:
        # Очищуємо буфер та завдання
        message_buffers.pop(chat_id, None)
        message_accumulator_tasks.pop(chat_id, None)

async def schedule_accumulated_reply(chat_id, message_text):
    """
    Планує відправку відповіді на накопичені повідомлення
    """
    # Додаємо повідомлення до буфера
    if chat_id not in message_buffers:
        message_buffers[chat_id] = []
    message_buffers[chat_id].append(message_text)
    
    # Оновлюємо час останньої активності
    last_activity_time[chat_id] = datetime.now()
    
    # Якщо вже є активне завдання для цього чату, скасовуємо його
    old_task = message_accumulator_tasks.get(chat_id)
    if old_task and not old_task.done():
        old_task.cancel()
    
    # Створюємо нове завдання, яке запустить обробку через 10 секунд
    new_task = asyncio.create_task(asyncio.sleep(10))
    message_accumulator_tasks[chat_id] = new_task
    
    try:
        await new_task
        # Після 10 секунд очікування - обробляємо повідомлення
        await process_accumulated_messages(chat_id)
    except asyncio.CancelledError:
        # Завдання скасовано (користувач продовжив писати)
        pass

# ===== Перевірка: чи є в чаті мої повідомлення (вся історія)
async def has_my_messages(chat_id):
    """
    Перевіряє, чи є в цьому чаті ХОЧА Б ОДНЕ моє повідомлення (навіть старше)
    Повертає True, якщо я колись писав у цей чат
    """
    try:
        # Шукаємо останні 100 повідомлень в чаті від мене
        async for message in client.iter_messages(chat_id, limit=50, from_user='me'):
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

@client.on(events.UserUpdate)
async def user_status_handler(event):
    global is_online, me, offline_since

    if me is None:
        return

    if event.user_id != me.id:
        return

    if isinstance(event.status, UserStatusOnline):
        is_online = True
        offline_since = None
        print("🟢 ONLINE — бот мовчить і скасовує заплановані повідомлення")

        for chat_id, task in list(scheduled_messages.items()):
            if not task.done():
                task.cancel()
                print(f"❌ Скасовано заплановане повідомлення для чату {chat_id}")

        scheduled_messages.clear()

    elif isinstance(event.status, UserStatusOffline):
        is_online = False
        offline_since = datetime.now()
        print("🔴 OFFLINE — старт відліку 2 хвилин")

# ===== Якщо ТИ сам написав — розблоковуємо чат та додаємо повідомлення до історії
@client.on(events.NewMessage(outgoing=True))
async def my_message_handler(event):
    if event.is_private:
        chat_id = event.chat_id
        
        # Розблоковуємо чат, якщо він був заблокований
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
        
        # Скасовуємо завдання на акумуляцію повідомлень для цього чату
        if chat_id in message_accumulator_tasks:
            task = message_accumulator_tasks[chat_id]
            if not task.done():
                task.cancel()
                print(f"❌ Скасовано завдання акумуляції для чату {chat_id} (ти написав)")
            message_accumulator_tasks.pop(chat_id, None)
            message_buffers.pop(chat_id, None)
        
        # Додаємо наше повідомлення до історії чату
        if chat_id not in chat_histories:
            chat_histories[chat_id] = []
        
        chat_histories[chat_id].append({
            "role": "assistant", 
            "content": event.text
        })
        
        # Зберігаємо лише останні 30 повідомлень
        if len(chat_histories[chat_id]) > 30:
            chat_histories[chat_id] = chat_histories[chat_id][-30:]

# ===== Автовідповіді
@client.on(events.NewMessage(incoming=True))
async def auto_reply_handler(event):
    if not event.is_private or not event.text or event.out:
        return
    
    sender = await event.get_sender()

    # ❌ якщо це бот — ігноруємо
    if sender.bot:
        print("🤖 Повідомлення від бота — ігнор")
        return
    
    # Якщо онлайн — мовчимо
    if is_online:
        return

    # Якщо офлайн менше 2 хвилин — мовчимо
    if offline_since is None:
        return

    if datetime.now() - offline_since < timedelta(minutes=2):
        print("⏳ OFFLINE менше 2 хв — ще не відповідаю")
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
        if DAIVINCHIK.search(text):
            # Запускаємо акумуляцію повідомлень для нового чату з ключовим словом
            await schedule_accumulated_reply(chat_id, text)
            print(f"🧠 Отримано повідомлення з 'дайвінчик' у новому чаті {chat_id}. Запущено акумуляцію повідомлень.")
            return
        else:
            # Стандартна відповідь для нового чату без ключового слова
            reply_text = "Привіт! Я зараз зайнятий, надіюсь не срочне повідомлення. Відповім як зможу!"
            await schedule_delayed_reply(chat_id, event, reply_text)
    
    else:  # Чат вже існуючий (я колись в ньому писав)
        # Для існуючого чату завжди акумулюємо повідомлення
        await schedule_accumulated_reply(chat_id, text)
        print(f"📨 Додано повідомлення до акумуляції для існуючого чату {chat_id}")

async def schedule_delayed_reply(chat_id, event, reply_text):
    """
    Запланована відправка стандартної відповіді через 1 хвилину
    """
    print(f"⏰ Заплановано відповідь для {chat_id} через 1 хв...")
    
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
            
            print(f"📤 Надсилаю заплановане повідомлення для {chat_id}")
            
            await client.send_message(
                chat_id,
                reply_text,
                reply_to=event.message.id
            )

            # 🔥 Повертаємо OFFLINE (Telegram сам робить ONLINE на мить)
            await client(UpdateStatusRequest(offline=True))

            last_reply_time[event.sender_id] = datetime.now()
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
    print("🤖 AFK-бот активний з покращеним режимом спілкування")
  

    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот зупинено")
