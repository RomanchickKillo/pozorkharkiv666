import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime
from html import escape

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8395537898:AAHulxjp_emBKGK62gwnWex0WdnfyleuC7I"
MAIN_ADMIN_ID = 5938341230
SUB_ADMIN_IDS = [8289191835]
ADMINS = [MAIN_ADMIN_ID] + SUB_ADMIN_IDS
CHANNEL_ID = -1004261539065

REQUIRED_CHANNEL_ID = -1004261539065
INVITE_LINK = "https://t.me/+k3e36HaejEM1ZWNi"

# Ссылки проекта
CHAT_LINK = "https://t.me/+FgvZTyggZAI3ZTYy"
PROJECT_CHANNEL_LINK = INVITE_LINK
BOT_LINK = "https://t.me/pozorkharkivbot"

# Путь к изображению главного меню (или file_id)
MAIN_MENU_IMAGE = "images/glavmenu.png"

# Максимальный размер видео в предложке (50 МБ)
MAX_VIDEO_SIZE = 50 * 1024 * 1024
TGK_INLINE = f'<a href="{INVITE_LINK}">НАШ ТГК</a>'

# Пути к файлам баз данных
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.txt")
POSTS_FILE = os.path.join(BASE_DIR, "posts.txt")
WARNS_FILE = os.path.join(BASE_DIR, "warns.json")
DRAFTS_FILE = os.path.join(BASE_DIR, "drafts.json")
STATS_FILE = os.path.join(BASE_DIR, "stats.json")
USERS_DATA_FILE = os.path.join(BASE_DIR, "users_data.json")

END_CHAT_BUTTON = "🔴 Закончить диалог"
LEGACY_END_CHAT_BUTTON = "❌ Закончить разговор"
# =============================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# Глобальные словари состояний в оперативной памяти
active_chats: dict[int, int] = {}
user_tickets: dict[int, str] = {}
pending_support: dict[int, dict] = {}
pending_posts: dict[int, dict] = {}

# Шаблоны быстрых ответов для админов
QUICK_REPLIES = {
    "⏱ Ожидайте": "Проблема решается, нужно подождать минутку.",
    "📩 Перешлите отмену": "Перешлите сообщение отмены с бота.",
    "🔄 Повторите заново": "Чтобы повторить предложку, повторите операцию заново.",
    "❓ Вопрос решен?": "Ваш вопрос решен или остались вопросы?",
}

RULES_TEXT = (
    "📜 <b>ПРАВИЛА И УСЛОВИЯ ИСПОЛЬЗОВАНИЯ БОТА</b>\n\n"
    "Перед использованием бота, пожалуйста, внимательно ознакомьтесь с нашими правилами:\n"
    "👉 <a href='https://telegra.ph/PRAVILA-PREDLOZHKI-POZOR-HARKOVA-08-26'>ПРАВИЛА КАНАЛА «ПОЗОР ХАРЬКОВА»</a>\n\n"
    "<i>Отправляя материалы в бота, вы автоматически соглашаетесь со всеми пунктами.</i>\n\n"
    "🔗 <b>Наши ресурсы:</b>\n"
    f'📢 <a href="{PROJECT_CHANNEL_LINK}">ПОЗОР ХАРЬКОВА</a>\n'
    f'💬 <a href="{CHAT_LINK}">НАШ ЧАТ</a>\n'
    f'🤖 <a href="{BOT_LINK}">НАШ БОТ</a>'
)


# ================= СОСТОЯНИЯ FSM =================
class Form(StatesGroup):
    photo = State()
    text = State()


class AdminForm(StatesGroup):
    reject_reason = State()


class SubAdminForm(StatesGroup):
    reject_reason = State()


class MainAdminForm(StatesGroup):
    edit_reason = State()


class AuthorCheckForm(StatesGroup):
    waiting_for_post = State()
    waiting_for_payment = State()


class SupportForm(StatesGroup):
    waiting_for_message = State()


class AdminPanelForm(StatesGroup):
    waiting_for_target = State()
    waiting_for_user_text = State()
    waiting_for_all_text = State()
    waiting_for_admins_text = State()


class DirectChatForm(StatesGroup):
    talking = State()


# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def in_active_chat(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in active_chats)


def get_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена и возврат в меню", callback_data="back_to_menu")]
        ]
    )


async def safe_delete_message(message: Message | None):
    """Безопасно удаляет сообщение, игнорируя ошибки Telegram API."""
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        pass


async def safe_edit_or_reply(message: Message, text: str, reply_markup=None):
    """Редактирует текст или подпись сообщения (если это медиа), иначе отправляет новое."""
    try:
        if message.text:
            await message.edit_text(text, reply_markup=reply_markup)
        elif message.caption is not None:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
        else:
            await message.answer(text, reply_markup=reply_markup)
    except Exception:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup)


# ================= УТИЛИТЫ JSON И БАЗ ДАННЫХ =================
def load_json(filepath: str, default_value):
    if not os.path.exists(filepath):
        return default_value
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Ошибка чтения JSON {filepath}: {e}")
        return default_value


def save_json(filepath: str, data):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Ошибка записи JSON {filepath}: {e}")


def init_stats() -> dict:
    return {
        "new_users": 0,
        "proposals": 0,
        "published": 0,
        "rejected": 0,
        "support": 0,
        "warns": 0,
        "photos": 0,
        "videos": 0,
        "admin_stats": {},
    }


def inc_stat(key: str, admin_id: int | None = None):
    stats = load_json(STATS_FILE, init_stats())
    if key in stats and isinstance(stats[key], int):
        stats[key] += 1
    if admin_id is not None:
        adm_str = str(admin_id)
        stats["admin_stats"][adm_str] = stats["admin_stats"].get(adm_str, 0) + 1
    save_json(STATS_FILE, stats)


async def daily_stats_task():
    """Фоновая задача для ежедневной отправки статистики в 20:00."""
    while True:
        now = datetime.now()
        if now.hour == 20 and now.minute == 0:
            stats = load_json(STATS_FILE, init_stats())
            text = (
                f"📊 <b>Статистика за день ({now.strftime('%d.%m.%Y')})</b>\n\n"
                f"👥 Новые пользователи: {stats.get('new_users', 0)}\n"
                f"📝 Предложек: {stats.get('proposals', 0)}\n"
                f"✅ Опубликовано: {stats.get('published', 0)}\n"
                f"❌ Отклонено: {stats.get('rejected', 0)}\n"
                f"📨 Обращений (поддержка): {stats.get('support', 0)}\n"
                f"🚫 Предупреждений выдано: {stats.get('warns', 0)}\n"
                f"📸 Фото в предложках: {stats.get('photos', 0)}\n"
                f"📹 Видео в предложках: {stats.get('videos', 0)}\n\n"
                f"👑 <b>Активность администраторов:</b>\n"
            )
            admin_stats = stats.get("admin_stats", {})
            if admin_stats:
                for adm_id, count in admin_stats.items():
                    text += f"ID <code>{adm_id}</code>: обработано {count}\n"
            else:
                text += "Нет активности за день.\n"

            for adm in ADMINS:
                try:
                    await bot.send_message(adm, text)
                except Exception:
                    pass

            save_json(STATS_FILE, init_stats())
            await asyncio.sleep(65)
        await asyncio.sleep(30)


# ================= MIDDLEWARE БАНА =================
class BanCheckMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id not in ADMINS:
            warns_data = load_json(WARNS_FILE, {})
            user_str = str(user.id)
            if warns_data.get(user_str, {}).get("banned", False):
                state = data.get("state")
                current_state = await state.get_state() if state else None

                is_allowed = False
                if isinstance(event, CallbackQuery) and event.data == "support_write":
                    is_allowed = True
                elif current_state == SupportForm.waiting_for_message.state:
                    is_allowed = True
                elif user.id in active_chats:
                    is_allowed = True
                elif isinstance(event, Message) and event.text:
                    if event.text in {"/end", "Закончить разговор", LEGACY_END_CHAT_BUTTON, END_CHAT_BUTTON}:
                        is_allowed = True
                    elif event.text.startswith("/start"):
                        is_allowed = True

                if not is_allowed:
                    reason = warns_data[user_str].get("reason", "Получено 3 предупреждения.")
                    msg_text = (
                        f"🚫 <b>Вы заблокированы.</b>\n"
                        f"Причина:\n{escape(reason)}\n\n"
                        "Если вы не согласны с решением, вы можете написать в поддержку."
                    )
                    kb = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="📞 НАПИСАТЬ В ПОДДЕРЖКУ", callback_data="support_write")]
                        ]
                    )
                    if isinstance(event, Message):
                        await event.answer(msg_text, reply_markup=kb)
                    elif isinstance(event, CallbackQuery):
                        if event.message:
                            await event.message.answer(msg_text, reply_markup=kb)
                        await event.answer()
                    return
        return await handler(event, data)


dp.message.middleware(BanCheckMiddleware())
dp.callback_query.middleware(BanCheckMiddleware())


# ================= ЧЕРНОВИКИ (АВТОСОХРАНЕНИЕ) =================
def save_draft(user_id: int, state: str, state_data: dict):
    drafts = load_json(DRAFTS_FILE, {})
    drafts[str(user_id)] = {
        "state": state,
        "data": state_data,
        "ts": time.time(),
    }
    save_json(DRAFTS_FILE, drafts)


def clear_draft(user_id: int):
    drafts = load_json(DRAFTS_FILE, {})
    if str(user_id) in drafts:
        del drafts[str(user_id)]
        save_json(DRAFTS_FILE, drafts)


def get_valid_draft(user_id: int) -> dict | None:
    drafts = load_json(DRAFTS_FILE, {})
    draft = drafts.get(str(user_id))
    if draft:
        if time.time() - draft.get("ts", 0) < 86400:
            return draft
        clear_draft(user_id)
    return None


# ================= РЕЕСТР ПОЛЬЗОВАТЕЛЕЙ И ПОСТОВ =================
def get_users_dict() -> dict[int, str]:
    users = {}
    if not os.path.exists(USERS_FILE):
        return users
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(":", 1)
            if len(parts) == 2 and parts[0].isdigit():
                users[int(parts[0])] = parts[1]
            elif len(parts) == 1 and parts[0].isdigit():
                users[int(parts[0])] = ""
    return users


def add_user(user_id: int, username: str):
    """Сохраняет или обновляет пользователя в реестре."""
    users = get_users_dict()
    username = username or ""
    is_new = user_id not in users
    if is_new or users.get(user_id, "") != username:
        users[user_id] = username
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            for uid, uname in users.items():
                f.write(f"{uid}:{uname}\n")
    if is_new:
        inc_stat("new_users")


def find_registered_user_id(identifier: str) -> int | None:
    """Ищет ID пользователя в базе по числовому ID или @username."""
    target = (identifier or "").strip()
    users = get_users_dict()
    if target.isdigit():
        user_id = int(target)
        return user_id if user_id in users else None
    target_username = target.lstrip("@").lower()
    if not target_username:
        return None
    for uid, username in users.items():
        if (username or "").lstrip("@").lower() == target_username:
            return uid
    return None


async def clear_participant_fsm(user_id: int):
    key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
    await dp.fsm.storage.set_state(key, None)
    await dp.fsm.storage.set_data(key, {})


def add_post_mapping(channel_msg_id: int, user_id: int, username: str):
    with open(POSTS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{channel_msg_id}:{user_id}:{username}\n")


def get_post_mapping(channel_msg_id: int) -> dict | None:
    if not os.path.exists(POSTS_FILE):
        return None
    with open(POSTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(":", 2)
            if len(parts) >= 3 and parts[0].isdigit():
                if int(parts[0]) == channel_msg_id:
                    return {"user_id": int(parts[1]), "username": parts[2]}
    return None


def count_user_posts(user_id: int) -> int:
    if not os.path.exists(POSTS_FILE):
        return 0
    count = 0
    target_str = str(user_id)
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(":", 2)
                if len(parts) >= 3 and parts[1] == target_str:
                    count += 1
    except Exception:
        pass
    return count


async def support_timeout_timer(user_id: int):
    await asyncio.sleep(86400)
    if user_id in pending_support:
        del pending_support[user_id]
        try:
            await bot.send_message(user_id, "⚠️ <b>Срок ожидания ответа по вашему обращению закончился.</b>")
        except Exception:
            pass


async def check_subscription(user_id: int) -> bool:
    if user_id in ADMINS:
        return True
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL_ID, user_id=user_id)
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except Exception as e:
        logging.error(f"Ошибка проверки подписки для {user_id}: {e}")
        return False


def with_tgk_link(text: str) -> str:
    if INVITE_LINK in text or ">НАШ ТГК<" in text:
        return text
    return f"{text}\n\n📢 {TGK_INLINE}"


def get_user_display_name(user) -> str:
    if getattr(user, "username", None):
        return f"@{escape(user.username)}"
    return escape(getattr(user, "full_name", None) or "Пользователь")


def get_main_menu_image():
    if not MAIN_MENU_IMAGE:
        return None
    if isinstance(MAIN_MENU_IMAGE, str) and os.path.isfile(MAIN_MENU_IMAGE):
        return FSInputFile(MAIN_MENU_IMAGE)
    return MAIN_MENU_IMAGE


def get_user_counts() -> tuple[int, int, int]:
    users = get_users_dict()
    warns_data = load_json(WARNS_FILE, {})
    banned = sum(1 for uid in users if warns_data.get(str(uid), {}).get("banned", False))
    total = len(users)
    active = max(total - banned, 0)
    return total, active, banned


async def send_post_content(chat_id: int, post_data: dict, reply_markup=None) -> int | None:
    """Отправляет предложку с учётом типа контента. Возвращает message_id основного сообщения."""
    caption = post_data.get("caption", "")
    media_type = post_data.get("media_type", "photo")

    if media_type == "video":
        msg = await bot.send_video(
            chat_id=chat_id,
            video=post_data["video"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return msg.message_id

    photos = post_data.get("photos", [])
    if not photos:
        raise ValueError("В предложке отсутствует медиа.")

    if len(photos) == 1:
        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=photos[0],
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        return msg.message_id

    media = [InputMediaPhoto(media=p) for p in photos]
    media[0].caption = caption
    media[0].parse_mode = "HTML"
    await bot.send_media_group(chat_id, media=media)

    if reply_markup:
        action = await bot.send_message(
            chat_id,
            "Выберите действие для поста выше:",
            reply_markup=reply_markup,
        )
        return action.message_id
    return None


async def publish_post_to_channel(post_data: dict) -> int:
    """Публикует предложку в канал и возвращает message_id главного сообщения."""
    caption = post_data.get("caption", "")
    if post_data.get("media_type") == "video":
        sent_msg = await bot.send_video(
            CHANNEL_ID,
            video=post_data["video"],
            caption=caption,
            parse_mode="HTML",
        )
        return sent_msg.message_id

    photos = post_data.get("photos", [])
    if len(photos) == 1:
        sent_msg = await bot.send_photo(
            CHANNEL_ID,
            photo=photos[0],
            caption=caption,
            parse_mode="HTML",
        )
        return sent_msg.message_id

    media = [InputMediaPhoto(media=p) for p in photos]
    media[0].caption = caption
    media[0].parse_mode = "HTML"
    sent_msgs = await bot.send_media_group(CHANNEL_ID, media=media)
    return sent_msgs[0].message_id


def get_media_keyboard(data: dict) -> InlineKeyboardMarkup:
    photos = data.get("photos", [])
    video = data.get("video")

    inline_kb = []
    if video:
        inline_kb.append([InlineKeyboardButton(text="✅ Всё верно", callback_data="photo_ok")])
        inline_kb.append([InlineKeyboardButton(text="🗑 Удалить видео", callback_data="photo_clear")])
    elif len(photos) > 0:
        inline_kb.append([InlineKeyboardButton(text="✅ Всё верно", callback_data="photo_ok")])
        if len(photos) < 3:
            inline_kb.append([InlineKeyboardButton(text="➕ Добавить ещё", callback_data="photo_add")])
        inline_kb.append([InlineKeyboardButton(text="🗑 Очистить", callback_data="photo_clear")])

    inline_kb.append([InlineKeyboardButton(text="🔙 Отмена и возврат в меню", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=inline_kb)


# ================= КОМАНДЫ АДМИНИСТРАТОРА =================
@dp.message(Command("help"))
async def cmd_help(message: Message):
    if message.from_user.id not in ADMINS:
        return
    text = (
        "📖 <b>Команды администратора</b>\n\n"
        "<code>/adm</code> — Основная панель управления (Кнопки)\n"
        "<code>/help</code> — Вызов этого меню с командами\n"
        "<code>/check [id/@username]</code> — Посмотреть профиль юзера (варны, предложки)\n"
        "<code>/chat [id/@username]</code> — Начать прямой диалог с пользователем\n"
        "<code>/warn [id/@username] [причина]</code> — Выдать предупреждение пользователю (3 варна = бан)\n"
        "<code>/unwarn [id/@username]</code> — Снять 1 предупреждение с пользователя\n"
        "<code>/get_data</code> — Скачать базы TXT и JSON (только для Гл. Админа)\n"
        "<code>/end</code> — Принудительно завершить диалог с пользователем\n"
        "<code>/rasilka</code> — Запустить меню рассылки пользователям"
    )
    await message.answer(text)


@dp.message(Command("chat"))
async def cmd_direct_chat(message: Message, state: FSMContext):
    admin_id = message.from_user.id
    if admin_id not in ADMINS:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("⚠️ Использование: <code>/chat ID или @username</code>")

    target_id = find_registered_user_id(parts[1])
    if target_id is None or target_id == admin_id:
        return await message.answer(
            "❌ Не удалось установить связь. Пользователь не найден в базе или указан неверный ID."
        )
    if admin_id in active_chats or target_id in active_chats:
        return await message.answer("⚠️ Один из участников уже находится в другом диалоге. Сначала завершите его.")

    await state.clear()
    await clear_participant_fsm(target_id)
    await state.set_state(DirectChatForm.talking)
    target_key = StorageKey(bot_id=bot.id, chat_id=target_id, user_id=target_id)
    await dp.fsm.storage.set_state(target_key, DirectChatForm.talking.state)
    active_chats[admin_id] = target_id
    active_chats[target_id] = admin_id

    user_keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=END_CHAT_BUTTON)]],
        resize_keyboard=True,
    )
    try:
        await bot.send_message(
            target_id,
            "👨‍💻 <b>ВНИМАНИЕ!</b>\nАдминистратор бота подключился к чату. Теперь вы можете общаться прямо здесь:",
            reply_markup=user_keyboard,
        )
    except Exception as exc:
        active_chats.pop(admin_id, None)
        active_chats.pop(target_id, None)
        await state.clear()
        await clear_participant_fsm(target_id)
        logging.info("Не удалось открыть прямой чат с %s: %s", target_id, exc)
        return await message.answer(
            "❌ Не удалось установить связь. Пользователь заблокировал бота или чат не найден."
        )

    target_username = get_users_dict().get(target_id, "")
    target_label = f"@{escape(target_username)}" if target_username else "Пользователь"
    admin_keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=END_CHAT_BUTTON)]],
        resize_keyboard=True,
    )
    await message.answer(
        f"✅ Связь с пользователем {target_label} (ID: <code>{target_id}</code>) успешно установлена.",
        reply_markup=admin_keyboard,
    )


@dp.message(Command("check"))
async def cmd_check(message: Message):
    if message.from_user.id not in ADMINS:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("⚠️ Использование: <code>/check @username или ID</code>")

    target_id = find_registered_user_id(parts[1])
    if not target_id:
        return await message.answer(f"❌ Пользователь {escape(parts[1])} не найден в базе.")

    warns_data = load_json(WARNS_FILE, {})
    user_str = str(target_id)
    user_warns = warns_data.get(user_str, {}).get("warns", 0)
    is_banned = warns_data.get(user_str, {}).get("banned", False)
    ban_reason = warns_data.get(user_str, {}).get("reason", "Нет")

    total_posts = count_user_posts(target_id)
    status = "🔴 Заблокирован" if is_banned else "🟢 Активен"

    text = (
        f"👤 <b>Профиль пользователя <code>{target_id}</code></b>\n\n"
        f"📊 Статус: <b>{status}</b>\n"
        f"⚠️ Варнов: <b>{user_warns}/3</b>\n"
        f"📝 Предложек: <b>{total_posts}</b>\n"
    )
    if is_banned:
        text += f"💬 Причина бана: {escape(ban_reason)}"

    await message.answer(text)


@dp.message(Command("warn"))
async def cmd_warn(message: Message):
    if message.from_user.id not in ADMINS:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        return await message.answer("⚠️ Использование: <code>/warn @username или ID Причина</code>")

    target_id = find_registered_user_id(parts[1])
    reason = parts[2]

    if not target_id:
        return await message.answer(f"❌ Пользователь {escape(parts[1])} не найден.")

    if target_id in ADMINS:
        return await message.answer("❌ <b>Ошибка:</b> Администраторы не могут выдавать предупреждения друг другу!")

    warns_data = load_json(WARNS_FILE, {})
    user_str = str(target_id)
    if user_str not in warns_data:
        warns_data[user_str] = {"warns": 0, "banned": False}

    warns_data[user_str]["warns"] += 1
    current_warns = warns_data[user_str]["warns"]
    inc_stat("warns")

    if current_warns >= 3:
        warns_data[user_str]["banned"] = True
        warns_data[user_str]["reason"] = reason
        save_json(WARNS_FILE, warns_data)
        await message.answer(f"✅ Пользователь <code>{target_id}</code> получил 3 предупреждения и был <b>заблокирован</b>.")

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📞 НАПИСАТЬ В ПОДДЕРЖКУ", callback_data="support_write")]
            ]
        )
        try:
            await bot.send_message(
                target_id,
                f"🚫 <b>Вы заблокированы.</b>\nПричина: Получено 3 предупреждения.\nПоследнее нарушение: {escape(reason)}\n\nЕсли вы не согласны с решением, вы можете написать в поддержку.",
                reply_markup=kb,
            )
        except Exception:
            pass
    else:
        save_json(WARNS_FILE, warns_data)
        await message.answer(
            f"✅ Пользователю <code>{target_id}</code> выдано предупреждение ({current_warns}/3).\nПричина: {escape(reason)}"
        )
        try:
            await bot.send_message(
                target_id,
                f"⚠️ <b>Вам выдано предупреждение.</b>\nПричина:\n{escape(reason)}\nПредупреждений: {current_warns}/3",
            )
        except Exception:
            pass


@dp.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    if message.from_user.id not in ADMINS:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("⚠️ Использование: <code>/unwarn @username или ID</code>")

    target_id = find_registered_user_id(parts[1])
    if not target_id:
        return await message.answer(f"❌ Пользователь {escape(parts[1])} не найден.")

    warns_data = load_json(WARNS_FILE, {})
    user_str = str(target_id)

    if user_str in warns_data and warns_data[user_str]["warns"] > 0:
        warns_data[user_str]["warns"] -= 1
        warns_data[user_str]["banned"] = False
        warns_data[user_str].pop("reason", None)
        save_json(WARNS_FILE, warns_data)
        await message.answer(
            f"✅ У пользователя <code>{target_id}</code> снято предупреждение. Текущее кол-во: {warns_data[user_str]['warns']}/3"
        )
        try:
            await bot.send_message(
                target_id,
                f"✅ Администратор снял с вас одно предупреждение. (Текущее: {warns_data[user_str]['warns']}/3)",
            )
        except Exception:
            pass
    else:
        await message.answer(f"У пользователя <code>{target_id}</code> нет предупреждений.")


def get_broadcast_kb(admin_id: int, back_callback: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="👥 Всем", callback_data="adm_br_all"),
            InlineKeyboardButton(text="👤 Пользователю", callback_data="adm_br_user"),
        ]
    ]
    if admin_id == MAIN_ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="🛡 Соо Админам", callback_data="adm_br_admins")])
    buttons.append([InlineKeyboardButton(text="🔙 Отмена", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("rasilka"))
async def cmd_rasilka(message: Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer(
        "📢 Выберите, кому нужно сделать рассылку:",
        reply_markup=get_broadcast_kb(message.from_user.id, "back_to_menu"),
    )


async def show_admin_panel(event: Message | CallbackQuery):
    kb_list = [
        [
            InlineKeyboardButton(text="📜 Правила", callback_data="adm_panel_rules"),
            InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_panel_broadcast"),
        ],
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="adm_panel_users"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="adm_panel_stats"),
        ],
        [InlineKeyboardButton(text="🛠 АДМ. КОМАНДЫ", callback_data="adm_panel_cmds")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_menu")],
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    text = "👑 <b>Панель управления админа</b>\n\nВыберите нужный раздел."

    if isinstance(event, Message):
        await event.answer(text, reply_markup=kb)
    else:
        if event.message.photo:
            await safe_delete_message(event.message)
            await event.message.answer(text, reply_markup=kb)
        else:
            await safe_edit_or_reply(event.message, text, reply_markup=kb)


@dp.message(Command("adm"))
async def cmd_adm(message: Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return
    await state.clear()
    await show_admin_panel(message)


@dp.callback_query(F.data == "open_admin_panel")
async def callback_admin_panel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS:
        return await callback.answer("❌ Доступ запрещен.", show_alert=True)
    await callback.answer()
    await state.clear()
    await show_admin_panel(callback)


# --- Обработчики админ панели ---
@dp.callback_query(F.data == "adm_panel_cmds")
async def adm_panel_cmds(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return
    await callback.answer()
    text = (
        "📖 <b>Команды администратора</b>\n\n"
        "<code>/adm</code> — Основная панель управления (Кнопки)\n"
        "<code>/help</code> — Вызов этого меню с командами\n"
        "<code>/check [id/@username]</code> — Посмотреть профиль юзера (варны, предложки)\n"
        "<code>/chat [id/@username]</code> — Начать прямой диалог с пользователем\n"
        "<code>/warn [id/@username] [причина]</code> — Выдать предупреждение\n"
        "<code>/unwarn [id/@username]</code> — Снять 1 предупреждение\n"
        "<code>/get_data</code> — Скачать базы (только для Гл. Админа)\n"
        "<code>/end</code> — Принудительно завершить диалог\n"
        "<code>/rasilka</code> — Запустить меню рассылки"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в админ панель", callback_data="open_admin_panel")]]
    )
    await safe_edit_or_reply(callback.message, text, reply_markup=kb)


@dp.callback_query(F.data == "adm_panel_stats")
async def adm_panel_stats_btn(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return
    total, active, banned = get_user_counts()
    stats = load_json(STATS_FILE, init_stats())
    text = (
        "📊 <b>Статистика проекта</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"🟢 Активных: <b>{active}</b>\n"
        f"🚫 Заблокировано: <b>{banned}</b>\n"
        f"📨 Предложек отправлено: <b>{stats.get('proposals', 0)}</b>\n"
        f"✅ Опубликовано: <b>{stats.get('published', 0)}</b>\n"
        f"❌ Отклонено: <b>{stats.get('rejected', 0)}</b>\n"
        f"📸 Фото: <b>{stats.get('photos', 0)}</b>\n"
        f"📹 Видео: <b>{stats.get('videos', 0)}</b>\n"
        f"📞 Обращений в поддержку: <b>{stats.get('support', 0)}</b>\n"
        f"⚠️ Выдано предупреждений: <b>{stats.get('warns', 0)}</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="open_admin_panel")]]
    )
    await safe_edit_or_reply(callback.message, text, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "adm_panel_users")
async def adm_panel_users(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return
    total, active, banned = get_user_counts()
    text = (
        "👥 <b>Пользователи</b>\n\n"
        f"Всего в базе: <b>{total}</b>\n"
        f"Активных: <b>{active}</b>\n"
        f"Заблокированных: <b>{banned}</b>\n\n"
        "Для детальной проверки отдельного пользователя используйте "
        "<code>/check @username</code> или <code>/check ID</code>."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="open_admin_panel")]]
    )
    await safe_edit_or_reply(callback.message, text, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "adm_panel_rules")
async def adm_panel_rules(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return
    await callback.message.answer(RULES_TEXT)
    await callback.answer()


@dp.callback_query(F.data == "adm_panel_broadcast")
async def adm_panel_broadcast(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return
    await callback.answer()
    await safe_edit_or_reply(
        callback.message,
        "📢 Выберите, кому нужно сделать рассылку:",
        reply_markup=get_broadcast_kb(callback.from_user.id, "open_admin_panel"),
    )


@dp.callback_query(F.data == "adm_br_user")
async def adm_br_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS:
        return
    await callback.answer()
    await safe_edit_or_reply(
        callback.message,
        "👤 Введите <b>@username</b> или <b>ID</b> пользователя:",
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(AdminPanelForm.waiting_for_target)


@dp.message(AdminPanelForm.waiting_for_target, F.text)
async def adm_br_target(message: Message, state: FSMContext):
    await state.update_data(target=message.text)
    await message.answer(
        "✍️ Отправьте сообщение для этого пользователя — текст, фото или GIF:",
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(AdminPanelForm.waiting_for_user_text)


@dp.message(AdminPanelForm.waiting_for_user_text)
async def adm_br_user_text(message: Message, state: FSMContext):
    data = await state.get_data()
    target_candidate = data["target"]
    target_id = find_registered_user_id(target_candidate)

    if not target_id:
        await message.answer(
            with_tgk_link(f"❌ Пользователь <b>{escape(target_candidate)}</b> не найден в базе данных.")
        )
    else:
        try:
            await message.send_copy(chat_id=target_id)
            await message.answer(with_tgk_link("✅ Сообщение успешно отправлено пользователю!"))
        except Exception as e:
            await message.answer(
                with_tgk_link(f"❌ Ошибка отправки (возможно бот заблокирован): {escape(str(e))}")
            )

    await state.clear()


@dp.callback_query(F.data == "adm_br_all")
async def adm_br_all(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS:
        return
    await callback.answer()
    await safe_edit_or_reply(
        callback.message,
        "👥 Отправьте сообщение для рассылки ВСЕМ пользователям — текст, фото или GIF:",
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(AdminPanelForm.waiting_for_all_text)


@dp.message(AdminPanelForm.waiting_for_all_text)
async def adm_br_all_text(message: Message, state: FSMContext):
    users = set(get_users_dict().keys())

    if not users:
        await state.clear()
        await message.answer(with_tgk_link("В базе пока нет ни одного пользователя."))
        return

    await message.answer(f"⏳ Начинаю рассылку для <b>{len(users)}</b> пользователей...")
    success, failed = 0, 0

    for user_id in users:
        try:
            await message.send_copy(chat_id=user_id)
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await message.answer(
        with_tgk_link(
            f"✅ <b>Рассылка завершена!</b>\n\n"
            f"Успешно доставлено: {success}\nНе доставлено: {failed}"
        )
    )
    await state.clear()


@dp.callback_query(F.data == "adm_br_admins")
async def adm_br_admins(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAIN_ADMIN_ID:
        return
    await callback.answer()
    await safe_edit_or_reply(
        callback.message,
        "🛡 Отправьте сообщение для <b>младших администраторов</b> — текст, фото или GIF:",
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(AdminPanelForm.waiting_for_admins_text)


@dp.message(AdminPanelForm.waiting_for_admins_text)
async def adm_br_admins_text(message: Message, state: FSMContext):
    if message.from_user.id != MAIN_ADMIN_ID:
        return

    success, failed = 0, 0
    for adm_id in SUB_ADMIN_IDS:
        try:
            if message.text:
                await bot.send_message(
                    adm_id,
                    f"🔔 <b>Сообщение от Главного Администратора:</b>\n\n{escape(message.text)}",
                )
            else:
                await bot.send_message(adm_id, "🔔 <b>Сообщение от Главного Администратора:</b>")
                await message.send_copy(chat_id=adm_id)
            success += 1
        except Exception:
            failed += 1

    await message.answer(
        f"✅ <b>Рассылка администраторам завершена!</b>\n"
        f"Успешно: {success}\nОшибок: {failed}"
    )
    await state.clear()


# ================= АВТО-ПРИЕМ ЗАЯВОК В КАНАЛ =================
@dp.chat_join_request()
async def auto_approve_join_request(chat_join: ChatJoinRequest, bot: Bot):
    try:
        await chat_join.approve()
        await bot.send_message(
            chat_join.from_user.id,
            "✅ <b>Ваша заявка в канал успешно принята!</b>\n\nТеперь вы можете пользоваться ботом.\n👉 Нажмите /start для открытия меню.",
        )
    except Exception as e:
        logging.error(f"Ошибка при одобрении заявки: {e}")


# ================= ГЛАВНОЕ МЕНЮ СТАРТА И ПРОВЕРКА =================
async def proceed_after_subscription(target: Message | CallbackQuery, user):
    """Общая логика после подтверждения подписки: проверка правил, черновиков и запуск меню."""
    user_id = user.id
    msg_obj = target if isinstance(target, Message) else target.message

    users_data = load_json(USERS_DATA_FILE, {})
    if not users_data.get(str(user_id), {}).get("agreed", False):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Я согласен", callback_data="rules_agree_start")]]
        )
        await msg_obj.answer(RULES_TEXT, reply_markup=kb)
        return

    draft = get_valid_draft(user_id)
    if draft:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="▶ Продолжить", callback_data="draft_continue")],
                [InlineKeyboardButton(text="🗑 Удалить", callback_data="draft_delete")],
            ]
        )
        await msg_obj.answer("📥 <b>У вас есть незавершённая предложка.</b>", reply_markup=kb)
        return

    add_user(user_id, user.username or "")
    await send_main_menu(target)


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    await state.clear()

    warns_data = load_json(WARNS_FILE, {})
    user_str = str(user_id)
    if user_id not in ADMINS and warns_data.get(user_str, {}).get("banned", False):
        reason = warns_data[user_str].get("reason", "Получено 3 предупреждения.")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📞 НАПИСАТЬ В ПОДДЕРЖКУ", callback_data="support_write")]]
        )
        return await message.answer(
            f"🚫 <b>Вы заблокированы.</b>\nПричина:\n{escape(reason)}\n\nЕсли вы не согласны с решением, вы можете написать в поддержку.",
            reply_markup=kb,
        )

    if not await check_subscription(user_id):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔄 ПРОВЕРИТЬ ПОДПИСКУ", callback_data="check_req_sub")]]
        )
        return await message.answer(
            with_tgk_link(
                "🔒 <b>Доступ ограничен</b>\n\n"
                f"Чтобы пользоваться ботом, необходимо подписаться на {TGK_INLINE}.\n"
                "После вступления нажмите кнопку <b>«ПРОВЕРИТЬ ПОДПИСКУ»</b>."
            ),
            reply_markup=kb,
        )

    await proceed_after_subscription(message, message.from_user)


@dp.callback_query(F.data == "rules_agree_start")
async def process_rules_agree_start(callback: CallbackQuery):
    user_id = callback.from_user.id
    users_data = load_json(USERS_DATA_FILE, {})
    users_data[str(user_id)] = {"agreed": True}
    save_json(USERS_DATA_FILE, users_data)

    add_user(user_id, callback.from_user.username or "")
    await safe_delete_message(callback.message)
    await send_main_menu(callback)


@dp.callback_query(F.data == "check_req_sub")
async def process_check_req_sub(callback: CallbackQuery):
    user_id = callback.from_user.id
    if await check_subscription(user_id):
        await safe_delete_message(callback.message)
        await callback.answer("✅ Подписка подтверждена!")
        await proceed_after_subscription(callback, callback.from_user)
    else:
        await callback.answer(
            "❌ Подписка пока не обнаружена. Откройте ссылку «НАШ ТГК», вступите в канал и повторите проверку.",
            show_alert=True,
        )


async def send_main_menu(event: Message | CallbackQuery):
    user_id = event.from_user.id

    kb_list = [
        [InlineKeyboardButton(text="📝 Предложить пост", callback_data="menu_propose")],
        [InlineKeyboardButton(text="👤 Мой профиль (Статус)", callback_data="menu_profile")],
        [InlineKeyboardButton(text="🔍 Узнать автора поста", callback_data="menu_find_author")],
        [InlineKeyboardButton(text="📞 Связь с поддержкой", callback_data="menu_support")],
    ]

    if user_id in ADMINS:
        kb_list.append([InlineKeyboardButton(text="👑 АДМИН ПАНЕЛЬ", callback_data="open_admin_panel")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    text = (
        "Привет! 👋 Добро пожаловать в бот канала <b>«ПОЗОР ХАРЬКОВА»</b>.\n\n"
        f"📢 <a href='{PROJECT_CHANNEL_LINK}'>ПОЗОР ХАРЬКОВА</a>\n"
        f"💬 <a href='{CHAT_LINK}'>НАШ ЧАТ</a>\n\n"
        f"Официальный канал: {TGK_INLINE}\n\n"
        "Выберите нужный пункт меню ниже:"
    )

    target_msg = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await safe_delete_message(event.message)

    main_menu_image = get_main_menu_image()
    if main_menu_image:
        await target_msg.answer_photo(photo=main_menu_image, caption=text, reply_markup=kb)
    else:
        await target_msg.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "menu_profile")
async def process_menu_profile(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    warns_data = load_json(WARNS_FILE, {})
    user_warns = warns_data.get(str(user_id), {}).get("warns", 0)

    has_pending = "Да (ожидает проверки модератором ⏳)" if user_id in pending_posts else "Нет 🤷‍♂️"
    total_posts = count_user_posts(user_id)
    user_label = get_user_display_name(callback.from_user)

    warn_note = "(Будьте осторожны, 3/3 — бан!)" if user_warns > 0 else "(У вас чистая репутация 😇)"
    text = (
        "👤 <b>Ваш профиль в боте</b>\n\n"
        f"👤 Пользователь: <b>{user_label}</b>\n"
        f"⚠️ Предупреждений: <b>{user_warns}/3</b> {warn_note}\n"
        f"🕒 Предложка в очереди: <b>{has_pending}</b>\n"
        f"📝 Мои предложки (за все время): <b>{total_posts}</b>\n\n"
        "<i>Следите за правилами, чтобы не получить блокировку в боте.</i>\n\n"
        f"📢 {TGK_INLINE}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 Правила бота", callback_data="show_rules_user")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")],
        ]
    )

    await safe_delete_message(callback.message)
    await callback.message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "back_to_menu")
async def process_back_to_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    clear_draft(callback.from_user.id)

    if not await check_subscription(callback.from_user.id):
        await safe_delete_message(callback.message)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔄 ПРОВЕРИТЬ ПОДПИСКУ", callback_data="check_req_sub")]]
        )
        return await callback.message.answer(
            with_tgk_link(
                "🔒 <b>Доступ ограничен</b>\n\n"
                f"Чтобы продолжить, подпишитесь на {TGK_INLINE} и повторите проверку."
            ),
            reply_markup=kb,
        )

    await send_main_menu(callback)


# --- Управление черновиками ---
@dp.callback_query(F.data == "draft_delete")
async def draft_delete(callback: CallbackQuery):
    await callback.answer()
    clear_draft(callback.from_user.id)
    await send_main_menu(callback)


@dp.callback_query(F.data == "draft_continue")
async def draft_continue(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    draft = get_valid_draft(callback.from_user.id)
    if not draft:
        return await send_main_menu(callback)

    await state.set_state(draft["state"])
    await state.update_data(**draft["data"])
    await safe_delete_message(callback.message)

    if draft["state"] == Form.photo.state:
        kb = get_media_keyboard(draft["data"])
        msg = await callback.message.answer(
            with_tgk_link("📸 Вы остановились на этапе загрузки медиа.\nПрикрепите фото или видео до 50 МБ или нажмите кнопку:"),
            reply_markup=kb,
        )
        await state.update_data(last_menu_id=msg.message_id)
    elif draft["state"] == Form.text.state:
        await callback.message.answer(
            "📝 Вы остановились на этапе текста. Отправьте описание одним сообщением:",
            reply_markup=get_cancel_kb(),
        )
    else:
        await send_main_menu(callback)


# ================= ПОДДЕРЖКА =================
@dp.callback_query(F.data == "menu_support")
async def process_menu_support(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    text = (
        "📞 <b>Прямая связь с поддержкой</b>\n\n"
        "Нажимая кнопку ниже, вы подтверждаете согласие с правилами.\n\n"
        "⏱ Обратите внимание: администратор рассмотрит ваше обращение и даст ответ <b>в течение 24 часов</b>.\n\n"
        "<i>Нажмите кнопку ниже, чтобы перейти к написанию сообщения.</i>\n\n"
        f"📢 {TGK_INLINE}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Написать поддержке", callback_data="support_write")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")],
        ]
    )
    await safe_delete_message(callback.message)
    await callback.message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "support_write")
async def process_support_write(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await safe_delete_message(callback.message)
    await callback.message.answer(
        "📝 Напишите ваше обращение в поддержку <b>одним текстовым сообщением</b>:",
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(SupportForm.waiting_for_message)


@dp.message(SupportForm.waiting_for_message, F.text)
async def process_support_message(message: Message, state: FSMContext):
    user = message.from_user
    username = f"@{escape(user.username)}" if user.username else f"ID: <code>{user.id}</code>"

    warns_data = load_json(WARNS_FILE, {})
    user_str = str(user.id)
    is_banned = warns_data.get(user_str, {}).get("banned", False)
    ban_reason = warns_data.get(user_str, {}).get("reason", "Не указана")

    ticket_id = f"№{random.randint(100000000, 999999999)}"
    user_tickets[user.id] = ticket_id

    kb_admin = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="▶️ Начать диалог", callback_data=f"supp_start:{user.id}")]]
    )

    pending_support[user.id] = {"ticket_id": ticket_id, "admin_messages": {}}
    escaped_msg = escape(message.text)

    if is_banned:
        admin_text = (
            f"🚨 <b>ЗАБЛОКИРОВАННЫЙ ПОЛЬЗОВАТЕЛЬ {username} прислал сообщение</b>\n"
            f"🎫 <b>Тикет:</b> {ticket_id}\n\n"
            f"<b>Причина бана:</b>\n{escape(ban_reason)}\n\n"
            f"<b>Текст:</b>\n{escaped_msg}"
        )
    else:
        admin_text = (
            f"📬 <b>Новое обращение в поддержку!</b>\n"
            f"🎫 <b>Тикет:</b> {ticket_id}\n"
            f"Пользователь: {username}\n\n"
            f"💬 <b>Текст обращения:</b>\n{escaped_msg}"
        )

    for admin_id in ADMINS:
        try:
            msg = await bot.send_message(admin_id, admin_text, reply_markup=kb_admin)
            pending_support[user.id]["admin_messages"][admin_id] = msg.message_id
        except Exception:
            pass

    asyncio.create_task(support_timeout_timer(user.id))
    inc_stat("support")

    await message.answer(
        f"✅ Ваше сообщение успешно отправлено!\n"
        f"🎫 <b>Ваш номер обращения: {ticket_id}</b>\n"
        "Ответ придет в этот чат в течение 24 часов."
    )
    await state.clear()


@dp.callback_query(F.data.startswith("supp_start:"))
async def admin_start_chat(callback: CallbackQuery, state: FSMContext):
    admin_id = callback.from_user.id
    if admin_id not in ADMINS:
        return
    user_id = int(callback.data.split(":")[1])

    if user_id not in pending_support:
        await callback.answer("⚠️ Это обращение уже взято другим администратором или истек срок.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    ticket_id = pending_support[user_id]["ticket_id"]
    for adm_id, msg_id in pending_support[user_id]["admin_messages"].items():
        if adm_id != admin_id:
            try:
                await bot.edit_message_text(
                    f"🛑 <b>Тикет {ticket_id} взят другим администратором.</b>",
                    chat_id=adm_id,
                    message_id=msg_id,
                )
            except Exception:
                pass
    del pending_support[user_id]

    active_chats[admin_id] = user_id
    active_chats[user_id] = admin_id

    if admin_id in SUB_ADMIN_IDS:
        try:
            await bot.send_message(
                MAIN_ADMIN_ID,
                f"ℹ️ <b>Уведомление:</b> Младший админ начал общение с пользователем <code>{user_id}</code> по тикету {ticket_id}.",
            )
        except Exception:
            pass

    await state.clear()
    await clear_participant_fsm(user_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    kb_user_chat = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=END_CHAT_BUTTON)]],
        resize_keyboard=True,
        persistent=True,
    )
    admin_chat_buttons = [
        [KeyboardButton(text="⏱ Ожидайте"), KeyboardButton(text="📩 Перешлите отмену")],
        [KeyboardButton(text="🔄 Повторите заново"), KeyboardButton(text="❓ Вопрос решен?")],
    ]
    if admin_id in SUB_ADMIN_IDS:
        admin_chat_buttons.append([KeyboardButton(text="🔄 Передать Гл. Админу")])
    admin_chat_buttons.append([KeyboardButton(text=END_CHAT_BUTTON)])
    kb_admin_chat = ReplyKeyboardMarkup(keyboard=admin_chat_buttons, resize_keyboard=True, persistent=True)

    await callback.message.reply(
        f"▶️ <b>Диалог по тикету {ticket_id} успешно начат!</b>\nВсе ваши сообщения теперь напрямую отправляются пользователю.",
        reply_markup=kb_admin_chat,
    )
    try:
        await bot.send_message(
            user_id,
            f"⚡ <b>Администратор подключился к диалогу! (Тикет {ticket_id})</b>",
            reply_markup=kb_user_chat,
        )
    except Exception:
        pass


@dp.message(F.text == "🔄 Передать Гл. Админу", StateFilter("*"))
async def transfer_to_main_admin(message: Message):
    admin_id = message.from_user.id
    if admin_id not in SUB_ADMIN_IDS or admin_id not in active_chats:
        return

    user_id = active_chats[admin_id]
    ticket_id = user_tickets.get(user_id, "№НЕИЗВЕСТНО")

    del active_chats[admin_id]
    del active_chats[user_id]

    await message.reply(f"✅ Вы передали вопрос {ticket_id} в поддержку Гл. Админу.", reply_markup=ReplyKeyboardRemove())
    try:
        await bot.send_message(
            user_id,
            f"⏳ Подождите минутку, мл. админ передал ваш вопрос <b>{ticket_id}</b> Гл. админу.",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        pass

    kb_main = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="▶️ Начать диалог", callback_data=f"supp_start:{user_id}")]]
    )
    pending_support[user_id] = {"ticket_id": ticket_id, "admin_messages": {}}
    try:
        msg = await bot.send_message(
            MAIN_ADMIN_ID,
            f"⚠️ <b>Младший админ передал вам тикет {ticket_id}!</b>",
            reply_markup=kb_main,
        )
        pending_support[user_id]["admin_messages"][MAIN_ADMIN_ID] = msg.message_id
    except Exception:
        pass


@dp.message(F.text.in_({"Закончить разговор", LEGACY_END_CHAT_BUTTON, END_CHAT_BUTTON}), StateFilter("*"))
@dp.message(Command("end"), StateFilter("*"))
async def cmd_end_chat(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in active_chats:
        return

    counterpart_id = active_chats.pop(user_id, None)
    if counterpart_id is not None:
        active_chats.pop(counterpart_id, None)

    await state.clear()
    if counterpart_id is not None:
        await clear_participant_fsm(counterpart_id)

    for participant_id in (user_id, counterpart_id):
        if participant_id is None:
            continue
        try:
            await bot.send_message(
                participant_id,
                "❌ <b>Диалог завершён.</b>",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception as exc:
            logging.info("Не удалось отправить уведомление о завершении диалога %s: %s", participant_id, exc)


@dp.message(in_active_chat, StateFilter("*"))
async def chat_message_forwarder(message: Message):
    counterpart_id = active_chats[message.from_user.id]

    if message.from_user.id in ADMINS and message.text in QUICK_REPLIES:
        text_to_send = QUICK_REPLIES[message.text]
        try:
            await bot.send_message(counterpart_id, text_to_send)
            await message.reply(f"<i>✅ Пользователю отправлено:</i>\n«{text_to_send}»")
        except Exception:
            await message.answer("⚠️ Не удалось доставить сообщение.")
        return

    try:
        await message.send_copy(chat_id=counterpart_id)
    except Exception:
        await message.answer("⚠️ Не удалось доставить сообщение собеседнику.")


# ================= ПРЕДЛОЖКА (ФОТО + ВИДЕО) =================
@dp.callback_query(F.data == "menu_propose")
async def process_menu_propose(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not await check_subscription(callback.from_user.id):
        return await process_back_to_menu(callback, state)

    await safe_delete_message(callback.message)

    await callback.message.answer(
        "Привет! 👋 Это бот предложки.\n\n"
        "Отправьте от 1 до 3 фотографий (можно отдельными сообщениями или альбомом) "
        "или одно видео размером до <b>50 МБ</b>.\n\n"
        f"📢 {TGK_INLINE}",
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(Form.photo)
    save_draft(callback.from_user.id, Form.photo.state, {"photos": [], "video": None, "media_type": "photo"})


@dp.message(Form.photo, F.photo)
async def process_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    video = data.get("video")
    photos = data.get("photos", [])

    if video:
        return await message.answer(
            with_tgk_link("⚠️ Сначала удалите выбранное видео, если хотите использовать фотографии."),
            reply_markup=get_media_keyboard(data),
        )

    if len(photos) >= 3:
        if not data.get("max_warned"):
            await message.answer(with_tgk_link("⚠️ Вы достигли лимита (3 фото)."))
            await state.update_data(max_warned=True)
        return

    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos, video=None, media_type="photo")
    save_draft(message.from_user.id, Form.photo.state, await state.get_data())

    last_menu_id = data.get("last_menu_id")
    if last_menu_id:
        try:
            await bot.delete_message(message.chat.id, last_menu_id)
        except Exception:
            pass

    kb = get_media_keyboard(await state.get_data())
    msg = await message.answer(
        with_tgk_link(f"📸 Загружено фотографий: <b>{len(photos)}/3</b>\n\nВы можете добавить еще фото или подтвердить."),
        reply_markup=kb,
    )
    await state.update_data(last_menu_id=msg.message_id)
    inc_stat("photos")


@dp.message(Form.photo, F.video)
async def process_video(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])

    if photos:
        return await message.answer(
            with_tgk_link("⚠️ Видео нельзя добавить к уже выбранным фотографиям. Очистите текущие фото и попробуйте снова."),
            reply_markup=get_media_keyboard(data),
        )

    file_size = message.video.file_size or 0
    if file_size > MAX_VIDEO_SIZE:
        size_mb = file_size / (1024 * 1024)
        return await message.answer(
            with_tgk_link(
                f"❌ <b>Видео слишком большое.</b>\n"
                f"Размер: <b>{size_mb:.1f} МБ</b>\n"
                "Максимальный размер: <b>50 МБ</b>."
            ),
            reply_markup=get_cancel_kb(),
        )

    await state.update_data(
        photos=[],
        video=message.video.file_id,
        media_type="video",
        max_warned=False,
    )
    save_draft(message.from_user.id, Form.photo.state, await state.get_data())

    await message.answer(
        with_tgk_link("✅ Видео загружено. Нажмите «Всё верно», чтобы продолжить."),
        reply_markup=get_media_keyboard(await state.get_data()),
    )
    inc_stat("videos")


@dp.message(Form.photo)
async def process_not_photo(message: Message):
    await message.answer(
        with_tgk_link("⚠️ Пожалуйста, отправляйте фотографии или одно видео не больше 50 МБ."),
        reply_markup=get_cancel_kb(),
    )


@dp.callback_query(F.data == "photo_add")
async def photo_add(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    last_menu_id = data.get("last_menu_id")
    if last_menu_id:
        try:
            await bot.delete_message(callback.message.chat.id, last_menu_id)
        except Exception:
            pass
    msg = await callback.message.answer(
        with_tgk_link("Жду следующее фото. Можно добавить ещё фото или очистить список и отправить одно видео до 50 МБ."),
        reply_markup=get_cancel_kb(),
    )
    await state.update_data(last_menu_id=msg.message_id)


@dp.callback_query(F.data == "photo_clear")
async def photo_clear(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(photos=[], video=None, media_type="photo", max_warned=False)
    save_draft(callback.from_user.id, Form.photo.state, await state.get_data())
    await safe_edit_or_reply(
        callback.message,
        with_tgk_link("🗑 Медиа очищены. Отправьте новые фотографии или одно видео до 50 МБ:"),
        reply_markup=get_cancel_kb(),
    )


@dp.callback_query(F.data == "photo_ok")
async def photo_ok(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("photos") and not data.get("video"):
        return await callback.answer("❌ Сначала отправьте хотя бы одно фото или видео!", show_alert=True)

    await callback.answer()
    await safe_delete_message(callback.message)
    await callback.message.answer(
        f"📝 Отлично! Теперь напишите описание для вашего поста одним сообщением.\n\n📢 {TGK_INLINE}",
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(Form.text)
    save_draft(callback.from_user.id, Form.text.state, data)


@dp.message(Form.text, F.text)
async def process_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    data = await state.get_data()
    save_draft(message.from_user.id, Form.text.state, data)

    caption_text = (
        f"❗Вам пришло сообщение:\n"
        f"{escape(data['text'])}\n\n"
        f"📢 {TGK_INLINE} | 💬 <a href='{CHAT_LINK}'>НАШ ЧАТ</a> | 🤖 <a href='{BOT_LINK}'>Бот для предложки</a>"
    )
    await state.update_data(caption=caption_text)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Все верно, отправить", callback_data="post_ok")],
            [
                InlineKeyboardButton(text="✏️ Изменить текст", callback_data="change_text"),
                InlineKeyboardButton(text="📸 Изменить медиа", callback_data="change_photo"),
            ],
            [InlineKeyboardButton(text="🔙 Отменить все", callback_data="back_to_menu")],
        ]
    )

    if data.get("media_type") == "video" and data.get("video"):
        await message.answer_video(
            video=data["video"],
            caption=caption_text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        photos = data.get("photos", [])
        if len(photos) == 1:
            await message.answer_photo(
                photo=photos[0],
                caption=caption_text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            media = [InputMediaPhoto(media=p) for p in photos]
            media[0].caption = caption_text
            media[0].parse_mode = "HTML"
            await bot.send_media_group(message.chat.id, media=media)
            await message.answer("Всё верно?", reply_markup=kb)


@dp.message(Form.text)
async def process_not_text(message: Message):
    await message.answer("Пожалуйста, отправь текстовое описание.", reply_markup=get_cancel_kb())


@dp.callback_query(F.data == "change_photo")
async def change_photo(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(photos=[], video=None, media_type="photo", max_warned=False)
    await safe_delete_message(callback.message)
    await callback.message.answer(
        with_tgk_link("Отправь новые фотографии или одно видео до 50 МБ:"),
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(Form.photo)


@dp.callback_query(F.data == "change_text")
async def change_text(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await safe_delete_message(callback.message)
    await callback.message.answer("Отправь новый текст:", reply_markup=get_cancel_kb())
    await state.set_state(Form.text)


@dp.callback_query(F.data == "post_ok")
async def post_ok(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    user = callback.from_user
    username = get_user_display_name(user)
    uname_clean = user.username if user.username else "None"

    if not data.get("photos") and not data.get("video"):
        return await callback.answer("❌ Медиа не найдено. Начните предложку заново.", show_alert=True)

    pending_posts[user.id] = {
        "photos": data.get("photos", []),
        "video": data.get("video"),
        "media_type": data.get("media_type", "photo"),
        "caption": data["caption"],
        "uname": uname_clean,
        "admin_messages": {},
    }

    kb_admin = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ ОПУБЛИКОВАТЬ В КАНАЛ", callback_data=f"adm_pub:{user.id}:{uname_clean}")],
            [InlineKeyboardButton(text="❌ ОТКАЗАТЬ", callback_data=f"adm_rej:{user.id}:{uname_clean}")],
            [InlineKeyboardButton(text="📖 Правила", callback_data="adm_panel_rules")],
        ]
    )

    post_data = pending_posts[user.id]
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, f"🚨 <b>Новая предложка от {username}!</b>")
            msg_id = await send_post_content(admin_id, post_data, reply_markup=kb_admin)
            pending_posts[user.id]["admin_messages"][admin_id] = msg_id
        except Exception as e:
            logging.error(f"Ошибка передачи предложки админу {admin_id}: {e}")

    kb_user = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Отправить еще пост", callback_data="send_more")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_menu")],
        ]
    )
    await safe_delete_message(callback.message)
    await callback.message.answer(
        with_tgk_link("✅ Ваша предложка успешно отправлена администраторам на проверку!"),
        reply_markup=kb_user,
    )

    clear_draft(user.id)
    inc_stat("proposals")
    await state.clear()


@dp.callback_query(F.data == "send_more")
async def process_send_more(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        with_tgk_link("Приложи фотографии или одно видео до 50 МБ для нового поста."),
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(Form.photo)
    save_draft(callback.from_user.id, Form.photo.state, {"photos": [], "video": None, "media_type": "photo"})


# ================= АДМИН-МОДЕРАЦИЯ =================
async def notify_other_admins_moderated(post_data: dict, current_admin_id: int, notice_text: str):
    """Убирает кнопки у остальных админов при обработке поста."""
    for adm_id, msg_id in post_data.get("admin_messages", {}).items():
        if adm_id != current_admin_id and msg_id:
            try:
                await bot.edit_message_reply_markup(chat_id=adm_id, message_id=msg_id, reply_markup=None)
                await bot.send_message(adm_id, notice_text, reply_to_message_id=msg_id)
            except Exception:
                pass


@dp.callback_query(F.data.startswith("adm_pub:"))
async def admin_approve(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        return
    await callback.answer()

    parts = callback.data.split(":", 2)
    user_id = int(parts[1])
    username = parts[2]

    post_data = pending_posts.pop(user_id, None)
    if not post_data:
        await callback.answer("❌ Этот пост уже обработан другим администратором.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await notify_other_admins_moderated(
        post_data,
        callback.from_user.id,
        "✅ Пост уже опубликован другим администратором.",
    )

    main_msg_id = await publish_post_to_channel(post_data)

    add_post_mapping(main_msg_id, user_id, username)
    now = datetime.now().strftime("%d.%m.%Y в %H:%M:%S")
    channel_link = f'<a href="{INVITE_LINK}">ПОЗОР ХАРЬКОВА</a>'

    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Ваш пост успешно одобрен и опубликован!</b>\n📅 Дата публикации: {now}\n\nПосмотреть можно здесь: {channel_link}",
        )
    except Exception:
        pass

    inc_stat("published", callback.from_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.reply(f"✅ Пост опубликован в канале!\nСвязь сохранена в базу. Пользователь уведомлен ({now}).")


@dp.callback_query(F.data.startswith("adm_rej:"))
async def admin_reject_start(callback: CallbackQuery, state: FSMContext):
    admin_id = callback.from_user.id
    if admin_id not in ADMINS:
        return
    await callback.answer()

    parts = callback.data.split(":", 2)
    user_id = int(parts[1])
    uname = parts[2]

    post_data = pending_posts.pop(user_id, None)
    if not post_data:
        await callback.answer("❌ Этот пост уже обработан другим администратором.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await notify_other_admins_moderated(
        post_data,
        admin_id,
        "❌ Пост уже взят на отклонение другим администратором.",
    )

    if admin_id == MAIN_ADMIN_ID:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚫 Отказать без причины", callback_data="adm_rej_no_reason")]]
        )
        prompt_msg = await callback.message.reply(
            "📝 <b>Напишите причину отклонения поста</b> следующим сообщением\nИЛИ нажмите кнопку ниже:",
            reply_markup=kb,
        )
        await state.update_data(
            reject_user_id=user_id,
            mod_chat_id=callback.message.chat.id,
            mod_message_id=callback.message.message_id,
            prompt_msg_id=prompt_msg.message_id,
            post_data=post_data,
        )
        await state.set_state(AdminForm.reject_reason)
    else:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚫 Отказать без причины", callback_data="sub_rej_no_reason")]]
        )
        prompt_msg = await callback.message.reply(
            "📝 Напишите причину отклонения для передачи <b>Главному Администратору</b>\nИЛИ нажмите кнопку ниже:",
            reply_markup=kb,
        )
        await state.update_data(
            reject_user_id=user_id,
            reject_uname=uname,
            mod_chat_id=callback.message.chat.id,
            mod_message_id=callback.message.message_id,
            prompt_msg_id=prompt_msg.message_id,
            post_data=post_data,
        )
        await state.set_state(SubAdminForm.reject_reason)


def get_reject_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📜 ПРАВИЛА", callback_data="show_rules_user")]]
    )


@dp.callback_query(F.data == "show_rules_user")
async def show_rules_user(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(RULES_TEXT)


@dp.callback_query(F.data == "adm_rej_no_reason", AdminForm.reject_reason)
async def admin_reject_no_reason(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAIN_ADMIN_ID:
        return
    await callback.answer()
    admin_data = await state.get_data()
    user_id = admin_data["reject_user_id"]
    now = datetime.now().strftime("%d.%m.%Y в %H:%M:%S")

    try:
        await bot.send_message(
            user_id,
            f"❌ <b>Ваш пост был отклонен модератором.</b>\n📅 <b>Время проверки:</b> {now}",
            reply_markup=get_reject_kb(),
        )
    except Exception:
        pass
    try:
        await bot.edit_message_reply_markup(
            chat_id=admin_data["mod_chat_id"],
            message_id=admin_data["mod_message_id"],
            reply_markup=None,
        )
    except Exception:
        pass

    inc_stat("rejected", callback.from_user.id)
    await callback.message.edit_text(f"❌ Пост отклонен без причины. Пользователь уведомлен.\nВремя: {now}")
    await state.clear()


@dp.message(AdminForm.reject_reason, F.text)
async def admin_reject_final(message: Message, state: FSMContext):
    if message.from_user.id != MAIN_ADMIN_ID:
        return
    admin_data = await state.get_data()
    user_id = admin_data["reject_user_id"]
    prompt_msg_id = admin_data.get("prompt_msg_id")
    reason = message.text
    now = datetime.now().strftime("%d.%m.%Y в %H:%M:%S")

    if prompt_msg_id:
        try:
            await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=prompt_msg_id, reply_markup=None)
        except Exception:
            pass
    try:
        await bot.send_message(
            user_id,
            f"❌ <b>Ваш пост был отклонен модератором.</b>\n📅 <b>Время проверки:</b> {now}\n💬 <b>Причина отказа:</b> {escape(reason)}",
            reply_markup=get_reject_kb(),
        )
    except Exception:
        pass
    try:
        await bot.edit_message_reply_markup(
            chat_id=admin_data["mod_chat_id"],
            message_id=admin_data["mod_message_id"],
            reply_markup=None,
        )
    except Exception:
        pass

    inc_stat("rejected", message.from_user.id)
    await message.reply(f"❌ Пост успешно отклонен. Пользователю отправлена причина.\nВремя: {now}")
    await state.clear()


@dp.callback_query(F.data == "sub_rej_no_reason", SubAdminForm.reject_reason)
async def sub_reject_no_reason(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await process_sub_admin_rejection(callback.message, state, "Без причины", is_callback=True)


@dp.message(SubAdminForm.reject_reason, F.text)
async def sub_reject_with_reason(message: Message, state: FSMContext):
    await process_sub_admin_rejection(message, state, message.text, is_callback=False)


async def process_sub_admin_rejection(message_obj: Message, state: FSMContext, reason: str, is_callback: bool):
    data = await state.get_data()
    user_id = data["reject_user_id"]
    uname = data["reject_uname"]
    prompt_msg_id = data.get("prompt_msg_id")
    post_data = data["post_data"]

    if prompt_msg_id:
        try:
            await bot.edit_message_reply_markup(chat_id=message_obj.chat.id, message_id=prompt_msg_id, reply_markup=None)
        except Exception:
            pass
    try:
        await bot.edit_message_reply_markup(
            chat_id=data["mod_chat_id"],
            message_id=data["mod_message_id"],
            reply_markup=None,
        )
    except Exception:
        pass

    text_to_main = (
        f"⚠️ <b>Внимание!</b> Младший администратор хочет отклонить пост от пользователя <code>{user_id}</code>.\n"
        f"💬 <b>Причина:</b> {escape(reason)}\n\nВыберите действие:"
    )
    kb_main = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отклонить окончательно", callback_data=f"main_rej_fin:{user_id}")],
            [InlineKeyboardButton(text="✏️ Отклонить с ред. причины", callback_data=f"main_rej_edit:{user_id}")],
            [InlineKeyboardButton(text="✅ ОДОБРИТЬ", callback_data=f"main_appr_sub:{user_id}:{uname}")],
        ]
    )

    pending_posts[user_id] = post_data
    await send_post_content(MAIN_ADMIN_ID, post_data, reply_markup=kb_main)
    await bot.send_message(MAIN_ADMIN_ID, text=text_to_main)

    if is_callback:
        await message_obj.edit_text("✅ Запрос на отклонение передан Главному Администратору.")
    else:
        await message_obj.reply("✅ Запрос на отклонение передан Главному Администратору.")
    await state.clear()


@dp.callback_query(F.data.startswith("main_rej_fin:"))
async def main_reject_final_btn(callback: CallbackQuery):
    if callback.from_user.id != MAIN_ADMIN_ID:
        return
    await callback.answer()
    user_id = int(callback.data.split(":")[1])
    now = datetime.now().strftime("%d.%m.%Y в %H:%M:%S")

    pending_posts.pop(user_id, None)
    try:
        await bot.send_message(
            user_id,
            f"❌ <b>Ваш пост был отклонен модератором.</b>\n📅 <b>Время проверки:</b> {now}",
            reply_markup=get_reject_kb(),
        )
    except Exception:
        pass

    inc_stat("rejected", callback.from_user.id)
    await safe_edit_or_reply(
        callback.message,
        "❌ Пост окончательно отклонен. Пользователь уведомлен без причины.",
    )


@dp.callback_query(F.data.startswith("main_rej_edit:"))
async def main_reject_edit_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAIN_ADMIN_ID:
        return
    await callback.answer()
    user_id = int(callback.data.split(":")[1])
    await safe_edit_or_reply(callback.message, "📝 Введите окончательную причину отклонения для пользователя:")
    await state.update_data(edit_user_id=user_id)
    await state.set_state(MainAdminForm.edit_reason)


@dp.message(MainAdminForm.edit_reason, F.text)
async def main_reject_edit_finish(message: Message, state: FSMContext):
    if message.from_user.id != MAIN_ADMIN_ID:
        return
    data = await state.get_data()
    user_id = data["edit_user_id"]
    reason = message.text
    now = datetime.now().strftime("%d.%m.%Y в %H:%M:%S")

    pending_posts.pop(user_id, None)
    try:
        await bot.send_message(
            user_id,
            f"❌ <b>Ваш пост был отклонен модератором.</b>\n📅 <b>Время проверки:</b> {now}\n💬 <b>Причина отказа:</b> {escape(reason)}",
            reply_markup=get_reject_kb(),
        )
    except Exception:
        pass

    inc_stat("rejected", message.from_user.id)
    await message.reply("❌ Пост отклонен. Отредактированная причина отправлена пользователю.")
    await state.clear()


@dp.callback_query(F.data.startswith("main_appr_sub:"))
async def main_approve_sub_btn(callback: CallbackQuery):
    if callback.from_user.id != MAIN_ADMIN_ID:
        return
    await callback.answer()
    parts = callback.data.split(":", 2)
    user_id = int(parts[1])
    uname = parts[2]

    post_data = pending_posts.pop(user_id, None)
    if not post_data:
        await callback.answer("❌ Данные поста не найдены в памяти.", show_alert=True)
        return

    main_msg_id = await publish_post_to_channel(post_data)

    add_post_mapping(main_msg_id, user_id, uname)
    now = datetime.now().strftime("%d.%m.%Y в %H:%M:%S")
    channel_link = f'<a href="{INVITE_LINK}">ПОЗОР ХАРЬКОВА</a>'

    try:
        await bot.send_message(
            user_id,
            f"🎉 <b>Ваш пост успешно одобрен и опубликован!</b>\n📅 Дата публикации: {now}\n\nПосмотреть можно здесь: {channel_link}",
        )
    except Exception:
        pass

    warning_text = "🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑🛑 <b>ГЛАВНЫЙ АДМИНИСТРАТОР ОДОБРИЛ ПОСТ, БУДЬ ВНИМАТЕЛЬНЕЕ В СЛЕДУЮЩИЙ РАЗ.</b>"
    for sub_admin in SUB_ADMIN_IDS:
        try:
            await bot.send_message(sub_admin, warning_text)
        except Exception:
            pass

    inc_stat("published", callback.from_user.id)
    await safe_edit_or_reply(
        callback.message,
        "✅ Вы одобрили пост в обход младшего админа! Пост опубликован.",
    )


# ================= АВТОР ПОСТА =================
@dp.callback_query(F.data == "menu_find_author")
async def find_author_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await safe_delete_message(callback.message)
    await callback.message.answer(
        "🔎 <b>ПОИСК АВТОРА ПОСТА</b>\n\n"
        f"Перешлите сюда сообщение из {TGK_INLINE}.\n"
        "Я проверю публикацию и, если она есть в базе, покажу найденного автора.\n\n"
        "⭐ <b>Стоимость услуги: 1000 звёзд</b>\n\n"
        "После пересылки бот автоматически проверит пост.",
        reply_markup=get_cancel_kb(),
    )
    await state.set_state(AuthorCheckForm.waiting_for_post)


@dp.message(AuthorCheckForm.waiting_for_post)
async def process_forwarded_post(message: Message, state: FSMContext):
    if not message.forward_from_chat:
        return await message.answer(
            "⚠️ Пожалуйста, именно <b>перешлите (forward)</b> пост из канала.",
            reply_markup=get_cancel_kb(),
        )

    if message.forward_from_chat.id != REQUIRED_CHANNEL_ID:
        return await message.answer("⚠️ Этот пост пришел не из нужного канала.", reply_markup=get_cancel_kb())

    msg_id = message.forward_from_message_id
    mapping = get_post_mapping(msg_id)
    if not mapping:
        await message.answer(
            "❌ К сожалению, этот пост не был найден в базе нашей предложки.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_menu")]]
            ),
        )
        await state.clear()
        return

    author_username = mapping["username"]
    author_text = "Пользователь скрыл ник" if author_username == "None" else f"@{escape(author_username)}"

    if message.from_user.id in ADMINS:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_menu")]]
        )
        await message.answer(
            f"✅ <b>Успешно найден создатель! (Бесплатно для Администратора)</b>\n\n"
            f"👤 <b>Создатель поста:</b> {author_text} (ID: <code>{mapping['user_id']}</code>)\n\n📢 {TGK_INLINE}",
            reply_markup=kb,
        )
        await state.clear()
        return

    await state.update_data(found_author=author_text)
    invoice_link = await bot.create_invoice_link(
        title="Узнать автора поста",
        description=f"Успешно найден создатель поста №{msg_id}! Оплатите услугу.",
        payload=f"check_author_pay:{message.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Stars", amount=1000)],
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Оплатить 1000 звезд", url=invoice_link)],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="back_to_menu")],
        ]
    )
    await message.answer(
        "✅ <b>Успешно найден создатель!</b>\n\nЧтобы раскрыть контакты, оплатите 1000 звезд ниже:",
        reply_markup=kb,
    )
    await state.set_state(AuthorCheckForm.waiting_for_payment)


@dp.pre_checkout_query()
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@dp.message(AuthorCheckForm.waiting_for_payment, F.successful_payment)
async def got_payment(message: Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_menu")]]
    )
    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"👤 <b>Создатель поста:</b> {data.get('found_author', 'Неизвестно')}\n\n"
        f"📢 {TGK_INLINE}",
        reply_markup=kb,
    )
    await state.clear()


# ================= ВЫГРУЗКА БАЗЫ (/get_data) =================
@dp.message(Command("get_data"))
async def cmd_get_data(message: Message):
    if message.from_user.id != MAIN_ADMIN_ID:
        return await message.answer("❌ Эта команда доступна только Главному Администратору.")

    files_to_send = [USERS_FILE, POSTS_FILE, STATS_FILE, WARNS_FILE, USERS_DATA_FILE]
    sent_files = 0

    status_msg = await message.answer("⏳ Собираю файлы баз данных...")

    for file_path in files_to_send:
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f:
                if file_path.endswith(".json"):
                    f.write("{}")

        try:
            await message.answer_document(document=FSInputFile(file_path))
            sent_files += 1
        except Exception as e:
            logging.error(f"Ошибка выгрузки {file_path}: {e}")
            await message.answer(f"⚠️ Не удалось выгрузить <code>{os.path.basename(file_path)}</code>: {escape(str(e))}")

    await status_msg.edit_text(f"✅ Успешно выгружено файлов: <b>{sent_files}/{len(files_to_send)}</b>")


# ================= ЗАПУСК БОТА =================
async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    print("Бот запущен!")
    asyncio.create_task(daily_stats_task())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
