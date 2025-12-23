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
# Таймери для акумуляції повідомлень
accumulation_timers = {}

ABOUT_ME = """
Мене звати Роман, мені 18 років.
Я  просто рома в якого мода не курити
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
    """
    instruction = """Ти — це я, реальний хлопець 18 років. Спілкуєшся в Телеграмі.
Відповідай природно, як у реальному діалозі. Зберігай контекст попередніх повідомлень.
Будь дружелюбним, цікавим співрозмовником."""
    
    if force_meet:
        instruction = "Перше повідомлення. Ти м'яко і приємно ініціюєш знайомство. Будь відкритим і дружелюбним."

    # Формуємо системне повідомлення
    system_message = {
        "role": "system",
        "content": f"""{instruction}

Факти про мене:
{ABOUT_ME}

Важливо:
1. Відповідай на всі питання та репліки з останніх повідомлень
2. Буди активним співрозмовником
3. Пиши українською, можна з емодзі 😊
4. Не будь занадто формальним"""
    }

    # Об'єднуємо системне повідомлення з історією діалогу
    messages_for_gpt = [system_message] + chat_history[-15:]  # Беремо останні 15 повідомлень

    try:
        response = client_ai.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_for_gpt,
            max_tokens=350,
            temperature=0.85,
            presence_penalty=0.1,
            frequency_penalty=0.1
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Помилка GPT: {e}")
        return "Зараз трохи зайнятий, але продовжуй писати 😊"

async def process_accumulated_messages(chat_id):
    """
    Обробляє накопичені повідомлення для чату та відправляє одну відповідь
    """
    # Перевіряємо, чи є щось в буфері
    if chat_id not in message_buffers or not message_buffers[chat_id]:
        message_buffers.pop(chat_id, None)
        accumulation_timers.pop(chat_id, None)
        return
    
    messages = message_buffers[chat_id].copy()
    print(f"🎯 Обробляю {len(messages)} накопичених повідомлень для чату {chat_id}")
    
    # Додаємо повідомлення до історії чату
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    
    # Додаємо всі накопичені повідомлення до історії
    for msg in messages:
        chat_histories[chat_id].append({"role": "user", "content": msg})
    
    # Перевіряємо, чи є у повідомленнях ключове слово "дайвінчик"
    force_meet = False
    all_messages_text = " ".join([msg.lower() for msg in messages])
    if DAIVINCHIK.search(all_messages_text):
        force_meet = True
    
    try:
        # Генеруємо відповідь
        reply_text = await generate_gpt_reply(chat_histories[chat_id], force_meet)
        
        # Відправляємо відповідь
        await client.send_message(chat_id, reply_text)
        print(f"✅ Відправили відповідь GPT у чат {chat_id}")
        
        # Додаємо відповідь до історії
        chat_histories[chat_id].append({"role": "assistant", "content": reply_text})
        
        # Оновлюємо статус
        await client(UpdateStatusRequest(offline=True))
        
        # Не блокуємо чат! Дозволяємо продовжити діалог
        # Просто оновлюємо час останньої відповіді
        last_reply_time[chat_id] = datetime.now()
        
        # Зберігаємо лише останні 20 повідомлень
        if len(chat_histories[chat_id]) > 20:
            chat_histories[chat_id] = chat_histories[chat_id][-20:]
            
    except Exception as e:
        print(f"❌ Помилка при відправці відповіді: {e}")
    finally:
        # Очищуємо буфер для цього чату
        message_buffers.pop(chat_id, None)
        accumulation_timers.pop(chat_id, None)

def start_accumulation_timer(chat_id):
    """
    Запускає таймер для акумуляції повідомлень
    """
    # Скасовуємо попередній таймер, якщо він є
    if chat_id in accumulation_timers:
        accumulation_timers[chat_id].cancel()
    
    # Створюємо новий таймер
    timer = asyncio.create_task(accumulation_timer_task(chat_id))
    accumulation_timers[chat_id] = timer

async def accumulation_timer_task(chat_id):
    """
    Завдання таймера: чекає 8 секунд, потім обробляє повідомлення
    """
    try:
        await asyncio.sleep(8)  # Чекаємо 8 секунд
        await process_accumulated_messages(chat_id)
    except asyncio.CancelledError:
        print(f"⏱️ Таймер для чату {chat_id} скасовано (нове повідомлення)")
    except Exception as e:
        print(f"❌ Помилка в таймері для чату {chat_id}: {e}")

async def add_message_to_accumulation(chat_id, message_text):
    """
    Додає повідомлення до буфера акумуляції
    """
    if chat_id not in message_buffers:
        message_buffers[chat_id] = []
    
    message_buffers[chat_id].append(message_text)
    print(f"📥 Додано повідомлення до буфера чату {chat_id} (всього: {len(message_buffers[chat_id])})")
    
    # Запускаємо/перезапускаємо таймер
    start_accumulation_timer(chat_id)

# ===== Перевірка: чи є в чаті мої повідомлення
async def has_my_messages(chat_id):
    """
    Перевіряє, чи є в цьому чаті ХОЧА Б ОДНЕ моє повідомлення
    """
    try:
        messages = await client.get_messages(chat_id, limit=5, from_user='me')
        if messages:
            return True
    except Exception as e:
        print(f"⚠️ Помилка перевірки чату {chat_id}: {e}")
    
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
                print(f"❌ Скасовано заплановане повідомлення для чату {chat_id}")
        scheduled_messages.clear()

        # Скасовуємо всі таймери акумуляції
        for chat_id, timer in list(accumulation_timers.items()):
            if not timer.done():
                timer.cancel()
                print(f"⏱️ Скасовано таймер акумуляції для чату {chat_id}")

    elif isinstance(event.status, UserStatusOffline):
        is_online = False
        offline_since = datetime.now()
        print("🔴 OFFLINE — бот активний через 2 хвилини")

# ===== Якщо ТИ сам написав
@client.on(events.NewMessage(outgoing=True))
async def my_message_handler(event):
    if event.is_private and event.text:
        chat_id = event.chat_id
        
        print(f"💬 Ви написали в чат {chat_id}")
        
        # Скасовуємо заплановане повідомлення для цього чату
        if chat_id in scheduled_messages:
            task = scheduled_messages[chat_id]
            if not task.done():
                task.cancel()
                print(f"❌ Скасовано заплановане повідомлення для чату {chat_id}")
            del scheduled_messages[chat_id]
        
        # Скасовуємо таймер акумуляції для цього чату
        if chat_id in accumulation_timers:
            timer = accumulation_timers[chat_id]
            if not timer.done():
                timer.cancel()
                print(f"⏱️ Скасовано таймер акумуляції для чату {chat_id}")
        
        # Очищуємо буфер повідомлень
        if chat_id in message_buffers:
            message_buffers.pop(chat_id)
            print(f"🧹 Очищено буфер повідомлень для чату {chat_id}")
        
        # Додаємо наше повідомлення до історії
        if chat_id not in chat_histories:
            chat_histories[chat_id] = []
        
        chat_histories[chat_id].append({
            "role": "assistant", 
            "content": event.text
        })
        
        # Зберігаємо лише останні 20 повідомлень
        if len(chat_histories[chat_id]) > 20:
            chat_histories[chat_id] = chat_histories[chat_id][-20:]

# ===== Автовідповіді на вхідні повідомлення
@client.on(events.NewMessage(incoming=True))
async def auto_reply_handler(event):
    # Перевірки
    if not event.is_private or not event.text or event.out:
        return
    
    sender = await event.get_sender()
    if sender.bot:
        return
    
    # Якщо онлайн — мовчимо
    if is_online:
        return
    
    # Якщо офлайн менше 2 хвилин — мовчимо
    if offline_since is None or datetime.now() - offline_since < timedelta(minutes=2):
        return

    chat_id = event.chat_id
    sender_id = event.sender_id
    text = event.text
    now = datetime.now()

    # Антиспам (30 секунд між відповідями GPT)
    if chat_id in last_reply_time:
        time_since_last = now - last_reply_time[chat_id]
        if time_since_last < timedelta(seconds=30):
            print(f"⏳ Чат {chat_id}: занадто швидко після останньої відповіді ({time_since_last.seconds}с)")
            return

    # ===== ОСНОВНА ЛОГІКА =====
    print(f"📨 Отримано повідомлення в чаті {chat_id}: {text[:50]}...")
    
    # Перевіряємо, чи це новий чат
    i_wrote_before = await has_my_messages(chat_id)
    
    if not i_wrote_before:
        # НОВИЙ чат - перевіряємо на "дайвінчик"
        if DAIVINCHIK.search(text.lower()):
            print(f"🎯 Новий чат {chat_id} з 'дайвінчик' - запускаємо акумуляцію")
            await add_message_to_accumulation(chat_id, text)
        else:
            # Новий чат без ключового слова - стандартна відповідь
            print(f"📝 Новий чат {chat_id} без ключового слова - стандартна відповідь")
            await schedule_standard_reply(chat_id, event)
    else:
        # ІСНУЮЧИЙ чат - завжди акумулюємо
        print(f"💾 Існуючий чат {chat_id} - додаємо до акумуляції")
        await add_message_to_accumulation(chat_id, text)

async def schedule_standard_reply(chat_id, event):
    """
    Запланована відправка стандартної відповіді через 1 хвилину
    (для нових чатів без ключового слова)
    """
    print(f"⏰ Заплановано стандартну відповідь для {chat_id} через 1 хв")
    
    async def send_delayed_message():
        try:
            # Чекаємо 60 секунд, перевіряючи кожні 5 секунд
            for i in range(12):
                await asyncio.sleep(5)
                if is_online:
                    print(f"🚫 Скасовано для чату {chat_id} (став ONLINE)")
                    return
            
            if is_online:
                return
            
            print(f"📤 Надсилаю стандартну відповідь для {chat_id}")
            
            await client.send_message(
                chat_id,
                "Привіт! Я зараз зайнятий, надіюсь не срочне повідомлення. Відповім як зможу!",
                reply_to=event.message.id
            )
            
            await client(UpdateStatusRequest(offline=True))
            last_reply_time[chat_id] = datetime.now()
            
            print(f"✅ Відправив стандартну відповідь у чат {chat_id}")
            
        except asyncio.CancelledError:
            print(f"❌ Задача для чату {chat_id} скасована")
        except Exception as e:
            print(f"❌ Помилка при відправці в чат {chat_id}: {e}")
        finally:
            scheduled_messages.pop(chat_id, None)
    
    task = asyncio.create_task(send_delayed_message())
    scheduled_messages[chat_id] = task

# ===== MAIN
async def main():
    global me

    await client.start() 
    me = await client.get_me()

    print(f"✅ Увійшов як: {me.first_name}")
    print("🤖 AFK-бот активний ")
   

    await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот зупинено")
