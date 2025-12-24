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

# ===== КОНФІГУРАЦІЯ =====
# Словник для зберігання історії діалогів: chat_id -> список повідомлень
chat_histories = {}
# Словник для зберігання завдань на "акумуляцію" повідомлень
message_accumulator_tasks = {}
# Словник для зберігання буфера повідомлень для акумуляції
message_buffers = {}
# Таймери для акумуляції повідомлень
accumulation_timers = {}
# Час останньої відповіді GPT в чаті
last_gpt_response_time = {}
# Час останнього повідомлення користувача в чаті
last_user_message_time = {}

ABOUT_ME = """
Мене звати Роман, мені 18 років.
Я просто ромка/ромашка в якого мода не курити.
Люблю спорт, програмування, гуляти, живе спілкування.
Пишу просто, тепло, без пафосу.
Спілкуюсь українською, іноді з емодзі.
"""

# ===== СТАНИ =====
is_online = False
me = None
scheduled_messages = {}  # заплановані повідомлення
offline_since = None  # час, коли ти пішов в офлайн

# Ключові слова для дайвінчика (бота знайомств)
DAIVINCHIK_KEYWORDS = re.compile(
    r'\b(дайвінчик|дайвінчика|знайомств|познайомити|познайом|знайом|хочу познайомити|шукаю знайомств|бот знайомств)\b',
    re.IGNORECASE
)

# Вітання
GREETINGS = re.compile(r'\b(привіт|вітаю|hello|hi|hey|ку|доброго дня|день добрий|добрий вечір|хай|йоу|здоров)\b', re.IGNORECASE)

client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH
)
BLOCKED_CHATS = {5885735578}
async def generate_gpt_reply(chat_history, is_daivin_chat=False):
    """
    Генерує відповідь на основі всієї історії діалогу в чаті.
    """
    if is_daivin_chat:
        instruction = """Ти — це я, Роман, 18 років. Ти в дайвінчику (боті знайомств) і хочеш познайомитися.
        
Як спілкуватися в дайвінчику:
1. Будь активним, цікавим співрозмовником
2. Задавай питання, щоб дізнатися про людину
3. Будь відкритим до знайомства
4. Показуй інтерес до співрозмовника
5. Не будь нав'язливим, але буди зацікавленим
6. Пропонуй теми для спілкування
7. Будь позитивним та дружелюбним
        
Факти про мене для знайомства:
- Мені 18 років
- Займаюся спортом (футбол, тренажерний зал)
- Цікавлюся програмуванням
- Люблю гуляти з друзями, відвідувати цікаві місця
- Не курю, іноді випиваю на свята
- Люблю слухати різну музику (але хоч узнайти класні пісні ще)
- Шукаю нових знайомств для спілкування і хочу найти дівчину
        
Будь природним, спілкуйся як реальна людина!"""
    else:
        instruction = """Ти — це я, Роман, 18 років. Спілкуєшся в Телеграмі.
Відповідай природно, як у реальному діалозі. Будь дружнім та відкритим."""

    # Формуємо системне повідомлення
    system_message = {
        "role": "system",
        "content": f"""{instruction}

Загальні факти про мене:
{ABOUT_ME}

Важливо:
1. Відповідай на всі питання та репліки
2. Будь активним співрозмовником
3. Пиши українською, можна з емодзі 😊
4. Буди природним, не формальним
5. Зберігай контекст розмови
6. Задавай питання для продовження діалогу"""
    }

    # Об'єднуємо системне повідомлення з історією діалогу
    messages_for_gpt = [system_message] + chat_history[-10:]  # Беремо останні 10 повідомлень для швидкості

    try:
        response = client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_gpt,
            max_tokens=400,
            temperature=0.9,  # Трохи вища температура для більш креативних відповідей
            presence_penalty=0.2,
            frequency_penalty=0.1
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Помилка GPT: {e}")
        return "Ого, класно пишеш! 😊 Продовжуй, слухаю..."

async def process_accumulated_messages(chat_id):
    """
    Обробляє накопичені повідомлення для чату та відправляє одну відповідь
    """
    if chat_id in BLOCKED_CHATS:
    return
    # Перевіряємо, чи є щось в буфері
    if chat_id not in message_buffers or not message_buffers[chat_id]:
        message_buffers.pop(chat_id, None)
        accumulation_timers.pop(chat_id, None)
        return
    
    messages = message_buffers[chat_id].copy()
    messages_count = len(messages)
    print(f"🎯 Обробляю {messages_count} накопичених повідомлень для чату {chat_id}")
    
    # Перевіряємо, чи це дайвінчик чат
    is_daivin_chat = False
    all_messages_text = " ".join([msg.lower() for msg in messages])
    if DAIVINCHIK_KEYWORDS.search(all_messages_text):
        is_daivin_chat = True
        print(f"💑 Чат {chat_id} - це дайвінчик (знайомства)")
    
    # Додаємо повідомлення до історії чату
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    
    # Додаємо всі накопичені повідомлення до історії
    for msg in messages:
        chat_histories[chat_id].append({"role": "user", "content": msg})
    
    try:
        # Генеруємо відповідь
        print(f"🧠 Генерую відповідь GPT для {messages_count} повідомлень...")
        reply_text = await generate_gpt_reply(chat_histories[chat_id], is_daivin_chat)
        
        # Відправляємо відповідь
        await client.send_message(chat_id, reply_text)
        print(f"✅ Відправили відповідь GPT у чат {chat_id} ({len(reply_text)} символів)")
        
        # Додаємо відповідь до історії
        chat_histories[chat_id].append({"role": "assistant", "content": reply_text})
        
        # Оновлюємо час останньої відповіді GPT
        last_gpt_response_time[chat_id] = datetime.now()
        print(f"⏰ Оновлено час останньої відповіді GPT для чату {chat_id}")
        
        # Оновлюємо статус
        await client(UpdateStatusRequest(offline=True))
        
        # Зберігаємо лише останні 15 повідомлень
        if len(chat_histories[chat_id]) > 15:
            chat_histories[chat_id] = chat_histories[chat_id][-15:]
            
    except Exception as e:
        print(f"❌ Помилка при відправці відповіді: {e}")
    finally:
        # Очищуємо буфер для цього чату
        message_buffers.pop(chat_id, None)
        accumulation_timers.pop(chat_id, None)

def start_accumulation_timer(chat_id, wait_time=8):
    """
    Запускає таймер для акумуляції повідомлень
    """
    # Скасовуємо попередній таймер, якщо він є
    if chat_id in accumulation_timers:
        accumulation_timers[chat_id].cancel()
    
    # Створюємо новий таймер
    async def timer_task():
        try:
            await asyncio.sleep(wait_time)
            await process_accumulated_messages(chat_id)
        except asyncio.CancelledError:
            pass
    
    timer = asyncio.create_task(timer_task())
    accumulation_timers[chat_id] = timer
    print(f"⏱️ Запущено таймер ({wait_time}с) для чату {chat_id}")

async def add_message_to_accumulation(chat_id, message_text, message_time=None):
    """
    Додає повідомлення до буфера акумуляції
    """
    if chat_id not in message_buffers:
        message_buffers[chat_id] = []
    
    message_buffers[chat_id].append(message_text)
    current_count = len(message_buffers[chat_id])
    
    # Оновлюємо час останнього повідомлення користувача
    last_user_message_time[chat_id] = message_time or datetime.now()
    
    print(f"📥 Додано повідомлення до буфера чату {chat_id} (всього: {current_count})")
    
    # Якщо це перше повідомлення або ми вже чекали достатньо - запускаємо таймер
    if current_count == 1:
        # Перше повідомлення - чекаємо 8 секунд
        start_accumulation_timer(chat_id, 8)
    else:
        # Якщо вже є повідомлення в буфері - перезапускаємо таймер на 5 секунд
        start_accumulation_timer(chat_id, 5)

# ===== Перевірка: чи є в чаті мої повідомлення
async def has_my_messages(chat_id):
    """
    Перевіряє, чи є в цьому чаті ХОЧА Б ОДНЕ моє повідомлення
    """
    try:
        messages = await client.get_messages(chat_id, limit=3, from_user='me')
        return len(messages) > 0
    except:
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
        print("🟢 ONLINE — бот мовчить")

        # Скасовуємо всі заплановані повідомлення
        for chat_id, task in list(scheduled_messages.items()):
            if not task.done():
                task.cancel()
        scheduled_messages.clear()

        # Скасовуємо всі таймери акумуляції
        for chat_id, timer in list(accumulation_timers.items()):
            if not timer.done():
                timer.cancel()

    elif isinstance(event.status, UserStatusOffline):
        is_online = False
        offline_since = datetime.now()
        print("🔴 OFFLINE — бот активний через 2 хвилини")

# ===== Якщо ТИ сам написав
@client.on(events.NewMessage(outgoing=True))
async def my_message_handler(event):
    if event.is_private and event.text:
        chat_id = event.chat_id
        
        print(f"💬 Ви написали в чат {chat_id}: {event.text[:50]}...")
        
        # Скасовуємо заплановане повідомлення для цього чату
        if chat_id in scheduled_messages:
            task = scheduled_messages[chat_id]
            if not task.done():
                task.cancel()
            del scheduled_messages[chat_id]
        
        # Скасовуємо таймер акумуляції для цього чату
        if chat_id in accumulation_timers:
            timer = accumulation_timers[chat_id]
            if not timer.done():
                timer.cancel()
        
        # Очищуємо буфер повідомлень
        if chat_id in message_buffers:
            message_buffers.pop(chat_id)
        
        # Додаємо наше повідомлення до історії
        if chat_id not in chat_histories:
            chat_histories[chat_id] = []
        
        chat_histories[chat_id].append({
            "role": "assistant", 
            "content": event.text
        })
        
        # Зберігаємо лише останні 15 повідомлень
        if len(chat_histories[chat_id]) > 15:
            chat_histories[chat_id] = chat_histories[chat_id][-15:]

# ===== Автовідповіді на вхідні повідомлення
@client.on(events.NewMessage(incoming=True))
async def auto_reply_handler(event):
    # Перевірки
    if not event.is_private or not event.text or event.out:
        return
    chat_id = event.chat_id
    if chat_id in BLOCKED_CHATS:
        print(f"🚫 Чат {chat_id} в BLOCKED_CHATS - ігноруємо повідомлення")
        return
    
    sender = await event.get_sender()
    if sender.bot:
        return
    
    # Якщо онлайн — мовчимо
    if is_online:
        return
    
    # Якщо офлайн менше 3 хвилин — мовчимо
    if offline_since is None or datetime.now() - offline_since < timedelta(minutes=3):
        return

    chat_id = event.chat_id
    text = event.text
    now = datetime.now()
    
    print(f"📨 Отримано повідомлення в чаті {chat_id}: {text[:50]}...")

    # ===== ЛОГІКА АКУМУЛЯЦІЇ =====
    # Перевіряємо, чи не надто швидко після останньої відповіді GPT
    if chat_id in last_gpt_response_time:
        time_since_last_gpt = now - last_gpt_response_time[chat_id]
        
        # Якщо минуло менше 30 секунд - додаємо до буфера і чекаємо
        if time_since_last_gpt < timedelta(seconds=30):
            print(f"⏳ Чат {chat_id}: занадто швидко після останньої відповіді GPT ({time_since_last_gpt.seconds}с)")
            print(f"   ↳ Додаємо до буфера і чекаємо...")
            await add_message_to_accumulation(chat_id, text, now)
            return
    
    # Перевіряємо, чи це новий чат
    i_wrote_before = await has_my_messages(chat_id)
    
    if not i_wrote_before:
        # НОВИЙ чат - перевіряємо на ключові слова дайвінчика
        if DAIVINCHIK_KEYWORDS.search(text.lower()):
            print(f"💑 Новий чат {chat_id} з ключовим словом дайвінчика")
            await add_message_to_accumulation(chat_id, text, now)
        elif GREETINGS.search(text.lower()):
            # Новий чат з вітанням - стандартна відповідь
            print(f"👋 Новий чат {chat_id} з вітанням - стандартна відповідь")
            await schedule_standard_reply(chat_id, event)
        else:
            # Новий чат без ключових слів - ігноруємо або дуже загальна відповідь
            print(f"🤷 Новий чат {chat_id} без ключових слів - ігноруємо")
    else:
        # ІСНУЮЧИЙ чат - завжди акумулюємо
        print(f"💾 Існуючий чат {chat_id} - додаємо до акумуляції")
        await add_message_to_accumulation(chat_id, text, now)

async def schedule_standard_reply(chat_id, event):
    """
    Запланована відправка стандартної відповіді через 1 хвилину
    (для нових чатів з вітанням без дайвінчика)
    """
    print(f"⏰ Заплановано стандартну відповідь для {chat_id} через 1 хв")
    
    async def send_delayed_message():
        try:
            # Чекаємо 60 секунд, перевіряючи кожні 5 секунд
            for i in range(12):
                await asyncio.sleep(5)
                if is_online:
                    return
            
            if is_online:
                return
            
            await client.send_message(
                chat_id,
                "Привіт! Зараз трохи зайнятий, відпишу пізніше ✌️",
                reply_to=event.message.id
            )
            
            await client(UpdateStatusRequest(offline=True))
            
            print(f"✅ Відправив стандартну відповідь у чат {chat_id}")
            
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"❌ Помилка: {e}")
        finally:
            scheduled_messages.pop(chat_id, None)
    
    task = asyncio.create_task(send_delayed_message())
    scheduled_messages[chat_id] = task

# ===== ФУНКЦІЯ ДЛЯ ПЕРЕВІРКИ ТА ОЧИЩЕННЯ СТАРИХ ДАНИХ =====
async def cleanup_old_data():
    """
    Періодично очищує старі дані зі словників
    """
    while True:
        await asyncio.sleep(3600)  # Кожну годину
        now = datetime.now()
        
        # Очищуємо старі чати з історії (старіші за 24 години)
        chats_to_remove = []
        for chat_id in list(chat_histories.keys()):
            if chat_id in last_user_message_time:
                time_since_last = now - last_user_message_time[chat_id]
                if time_since_last > timedelta(hours=24):
                    chats_to_remove.append(chat_id)
        
        for chat_id in chats_to_remove:
            chat_histories.pop(chat_id, None)
            last_user_message_time.pop(chat_id, None)
            last_gpt_response_time.pop(chat_id, None)
            print(f"🧹 Очищено дані для старого чату {chat_id}")

# ===== MAIN
async def main():
    global me

    await client.start() 
    me = await client.get_me()

    print(f"✅ Увійшов як: {me.first_name}")
    print("🤖 ДАЙВІНЧИК БОТ АКТИВНИЙ 💑")
   

    # Запускаємо фонову задачу для очищення даних
    asyncio.create_task(cleanup_old_data())

    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот зупинено")
