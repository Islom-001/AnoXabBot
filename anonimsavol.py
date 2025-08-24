import os
import sqlite3
import base64
import json
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MessageEntity, Poll
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import telegram.error
import html
import urllib.parse

BOT_USERNAME = "AnonimXabarliBot"  # Masalan: AnonimSavolBot
BOT_TOKEN = os.getenv('BOT_TOKEN')  # Eski hardcoded ni o'rniga
ADMIN_ID = int(os.getenv('ADMIN_ID'))  # Eski hardcoded ni o'rniga

# SQLite bazasiga ulanish
def get_db_connection():
    conn = sqlite3.connect("bot.db")
    conn.row_factory = sqlite3.Row
    return conn

# Jadvalarni yaratish va migration
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)''')
    
    # Migration: Add language and referrals if not exists
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'language' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'uz'")
    if 'referrals' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN referrals INTEGER DEFAULT 0")
    if 'custom_ref' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_ref TEXT")
    if 'first_name' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
    if 'username' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
    
    # Create unique index for custom_ref
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS custom_ref_idx ON users (custom_ref)")
    
    # Banned users
    cursor.execute('''CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY)''')
    
    # User-specific blacklists
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_blacklists (
        blocker_id INTEGER, blocked_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (blocker_id, blocked_id)
    )''')
    
    # Channels
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels (id TEXT PRIMARY KEY, link TEXT, name TEXT)''')
    
    # Messages
    cursor.execute('''CREATE TABLE IF NOT EXISTS messages (
        message_id TEXT PRIMARY KEY, sender_id INTEGER, receiver_id INTEGER, text TEXT,
        media_type TEXT, file_id TEXT, caption TEXT,
        sender_name TEXT, sender_username TEXT, receiver_name TEXT, receiver_username TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Sessions
    cursor.execute('''CREATE TABLE IF NOT EXISTS sessions (
        user_id INTEGER PRIMARY KEY, step TEXT, data TEXT
    )''')
    
    # Referrals
    cursor.execute('''CREATE TABLE IF NOT EXISTS referrals (
        referrer_id INTEGER, referred_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (referrer_id, referred_id)
    )''')
    
    conn.commit()
    conn.close()

init_db()

def encode_user_id(uid: int) -> str:
    return base64.b64encode(str(uid).encode()).decode()

def decode_user_id(code: str) -> int:
    try:
        return int(base64.b64decode(code.encode()).decode())
    except Exception:
        raise ValueError("Noto'g'ri havola kodi")

def get_user_from_ref(code: str) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE custom_ref = ?", (code,))
        row = cursor.fetchone()
        if row:
            return row['id']
        try:
            decoded = decode_user_id(code)
            cursor.execute("SELECT id FROM users WHERE id = ? AND custom_ref IS NULL", (decoded,))
            row = cursor.fetchone()
            if row:
                return decoded
        except:
            pass
    raise ValueError("Noto'g'ri havola kodi")

def get_ref_link(user_id: int) -> str:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT custom_ref FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        custom_ref = row['custom_ref'] if row else None
    if custom_ref:
        return f"https://t.me/{BOT_USERNAME}?start={custom_ref}"
    else:
        return f"https://t.me/{BOT_USERNAME}?start={encode_user_id(user_id)}"

def add_user_to_db(user_id: int, language='uz', first_name=None, username=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (id, language, first_name, username) VALUES (?, ?, ?, ?)", (user_id, language, first_name, username))
        conn.commit()

def update_user_info(user_id: int, first_name: str, username: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET first_name = ?, username = ? WHERE id = ?", (first_name, username, user_id))
        conn.commit()

def update_user_language(user_id: int, language: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET language = ? WHERE id = ?", (language, user_id))
        conn.commit()

def get_user_language(user_id: int) -> str:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT language FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return row['language'] if row else 'uz'

def is_user_banned(user_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        return bool(cursor.fetchone())

def is_user_blocked(blocker_id: int, blocked_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM user_blacklists WHERE blocker_id = ? AND blocked_id = ?", (blocker_id, blocked_id))
        return bool(cursor.fetchone())

def block_user(blocker_id: int, blocked_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO user_blacklists (blocker_id, blocked_id) VALUES (?, ?)", (blocker_id, blocked_id))
        conn.commit()

def unblock_user(blocker_id: int, blocked_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_blacklists WHERE blocker_id = ? AND blocked_id = ?", (blocker_id, blocked_id))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted

def clear_blacklist(blocker_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_blacklists WHERE blocker_id = ?", (blocker_id,))
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count

def get_blacklist_count(blocker_id: int) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM user_blacklists WHERE blocker_id = ?", (blocker_id,))
        return cursor.fetchone()[0]

def ban_user(user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,))
        conn.commit()

def unban_user(user_id: int) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted

def is_valid_url(url: str) -> bool:
    regex = r'^https?://[^\s/$.?#].[^\s]*$'
    return bool(re.match(regex, url))

def is_valid_channel_id(input_str: str) -> bool:
    return input_str.startswith("@") or input_str.startswith("-100")

def is_valid_invite_link(link: str) -> bool:
    return link.startswith("https://t.me/+") or link.startswith("https://t.me/")

def is_valid_custom_ref(ref: str) -> bool:
    regex = r'^[a-z0-9_]{3,20}$'
    return bool(re.match(regex, ref))

def serialize_entity(entity) -> dict:
    return {
        "type": entity.type,
        "offset": entity.offset,
        "length": entity.length,
        "url": getattr(entity, "url", None),
        "user": getattr(entity, "user", None) and entity.user.id,
        "language": getattr(entity, "language", None),
        "custom_emoji_id": getattr(entity, "custom_emoji_id", None)
    }

def serialize_poll(poll: Poll) -> dict:
    return {
        "question": poll.question,
        "options": [option.text for option in poll.options],
        "is_anonymous": poll.is_anonymous,
        "allows_multiple_answers": poll.allows_multiple_answers,
        "type": poll.type
    }

async def check_channel_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM channels")
        channels = [row["id"] for row in cursor.fetchall()]
    if not channels:
        return True
    for channel_id in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            return False
    return True

async def get_channels_keyboard(lang='uz') -> InlineKeyboardMarkup:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT link FROM channels")
        links = [row["link"] for row in cursor.fetchall()]
    join_text = "Qo'shilish" if lang == 'uz' else "Join" if lang == 'en' else "Присоединиться"
    check_text = "Tekshirish ✅" if lang == 'uz' else "Check ✅" if lang == 'en' else "Проверить ✅"
    keyboard = [[InlineKeyboardButton(join_text, url=link)] for link in links]
    keyboard.append([InlineKeyboardButton(check_text, callback_data="check_membership")])
    return InlineKeyboardMarkup(keyboard)

async def set_bot_commands(context: ContextTypes.DEFAULT_TYPE):
    commands = [
        BotCommand(command="start", description="✨ Referal havolangizni olish uchun"),
        BotCommand(command="lang", description="🏳️ Bot tilini tanlash"),
        BotCommand(command="mystats", description="📊 Profil statistikangizni ko'rish"),
        BotCommand(command="blacklist", description="📜 Qora ro‘yxatni ko'rish "),
        BotCommand(command="url", description="🔗 Referal linkni o'zgartirish")
    ]
    await context.bot.set_my_commands(commands)

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# Translations
translations = {
    'uz': {
        'banned': "Siz botdan foydalana olmaysiz, chunki bloklangansiz.",
        'subscribe_channels': "Botdan foydalanish uchun quyidagi kanallarga a‘zo bo‘ling: 😊",
        'own_link': "<b>Bu sizning shaxsiy havolangiz:</b>\n\n{ref_link}\n\n<b>Ulashish orqali anonim suhbat quring! 😊</b>",
        'self_message': "O‘zingizga xabar yubora olmaysiz!",
        'user_banned': "Bu foydalanuvchi sizni bloklagan.",
        'invalid_link': "Xatolik! Havola noto‘g‘ri bo‘lishi mumkin.",
        'send_message': "<b>Murojaatingizni shuyerga yozing!</b>",
        'admin_only': "Bu buyruq faqat admin uchun!",
        'admin_panel': "Admin paneli: Quyidagi amallarni tanlang",
        'stats': "📊 *Bot Statistikasi*\n\n👥 Umumiy foydalanuvchilar: {users_count}\n🚫 Bloklangan foydalanuvchilar: {banned_users_count}\n💬 Yuborilgan xabarlar: {messages_count}",
        'apk_banned': "<b>Kechirasiz, .apk fayllarini yuborish taqiqlangan!</b>",
        'new_message': "📨 Sizga yangi anonim xabar bor!\n\n{text}",
        'message_sent': "Xabaringiz anonim tarzda yuborildi ✅",
        'use_link_first': "Iltimos, avval shaxsiy havoladan foydalaning.",
        'reply_message': "Anonim javob:\n\n{text}",
        'reply_sent': "Javobingiz anonim tarzda yuborildi ✅",
        'broadcast_prompt': "Xabarga inline tugmalar qo‘shmoqchimisiz?",
        'button_count_prompt': "Iltimos, 1-10 oralig‘ida tugma sonini kiriting.",
        'invalid_number': "Iltimos, to‘g‘ri raqam kiriting (masalan, 2).",
        'button_name_prompt': "{current}-tugma nomini kiriting (umumiy {total} ta tugma):",
        'button_url_prompt': "{current}-tugma uchun havolani kiriting:",
        'invalid_url': "Iltimos, to‘g‘ri URL kiriting (masalan, https://example.com).",
        'broadcast_sent': "Xabar {success} foydalanuvchiga yuborildi.\nMuvaffaqiyatsiz: {failed}",
        'forward_sent': "Forward xabar {success} foydalanuvchiga yuborildi.\nMuvaffaqiyatsiz: {failed}",
        'channel_count_prompt': "Iltimos, 1-10 oralig‘ida kanal sonini kiriting.",
        'channel_id_prompt': "{current}-kanal ID sini kiriting:",
        'invalid_channel_id': "Iltimos, to‘g‘ri kanal ID sini kiriting (masalan, @KanalUsername yoki -100123456789).",
        'channel_link_prompt': "{current}-kanal uchun invite linkni kiriting (masalan, https://t.me/+ABCDEF):",
        'invalid_invite_link': "Iltimos, to‘g‘ri invite link kiriting (masalan, https://t.me/+ABCDEF).",
        'channels_set': "{count} ta kanal muvaffaqiyatli o‘rnatildi.",
        'channels_removed': "Majburiy kanallar o‘chirildi. Endi bot obunasiz ishlaydi.",
        'thanks_subscribed': "Rahmat! Endi botdan foydalana olasiz.",
        'reply_prompt': "<b>Javobingizni yozing yoki media yuboring, u anonim tarzda yuboriladi!</b>",
        'message_not_found': "Xatolik! Xabar topilmadi.",
        'block': "Bloklash",
        'block_sent': "<b>Foydalanuvchi muvaffaqiyatli bloklandi. ✅</b>\n\n<blockquote>/blacklist - qora ro‘yxatni tozalash</blockquote>",
        'unblock': "Blokdan chiqarish",
        'blacklist': "Sizning qora ro‘yxatingizda {count} ta foydalanuvchi bor",
        'clear_blacklist': "Qora ro‘yxatni tozalash 🗑",
        'blacklist_cleared': "Qora ro‘yxat muvaffaqiyatli tozalandi.",
        'broadcast_message_prompt': "Barcha foydalanuvchilarga yuboriladigan xabarni yoki mediayni kiriting:",
        'forward_message_prompt': "Forward qilinadigan xabarni yoki mediayni yuboring:",
        'ban_usage': "Iltimos, foydalanuvchi ID sini kiriting: /ban <user_id>",
        'banned_user': "Foydalanuvchi {user_id} bloklandi.",
        'unban_usage': "Iltimos, foydalanuvchi ID sini kiriting: /unban <user_id>",
        'unbanned_user': "Foydalanuvchi blokdan chiqarildi.",
        'not_banned': "Foydalanuvchi bloklanmagan edi.",
        'warn_usage': "Iltimos, foydalanuvchi ID sini kiriting: /warn <user_id>",
        'warned_user': "Foydalanuvchi {user_id} ga ogohlantirish yuborildi.",
        'error_id': "Xatolik! ID noto‘g‘ri bo‘lishi mumkin.",
        'warn_message': "<b>Ogohlantirish! Ustingizdan shikoyat tushdi, yana takrorlansa bloklanishingiz mumkin!</b>",
        'lang_prompt': "Bot qaysi tilda ishlashini tanlang",
        'mystats': "<b>📌 Profil statistikasi</b>\n\n<b>Bugun:</b>\n<blockquote>💬 Xabarlar: {today_messages}\n👀 Link orqali o‘tishlar: {today_referrals}\n⭐️ Mashhurlik: {popularity_rank} o‘rin</blockquote>\n\n<b>Umumiy:</b>\n<blockquote>💬 Xabarlar: {total_messages}\n👀 Link orqali o‘tishlar: {total_referrals}\n⭐️ Mashhurlik: {popularity_rank} o‘rin</blockquote>\n\n⭐️ Mashhurlik darajasini ko‘tarish uchun shaxsiy linkingizni tarqating:\n👉 {ref_link}",
        'share_button': "Ulashish",
        'share_post': "Ushbu link orqali menga anonim xabar yuborishingiz mumkin😊\n\n👉🏻 {ref_link}",
        'media_error': "Media yuborishda xato yuz berdi, lekin matn yuborildi:\n\n",
        'url_usage': "Iltimos, yangi linkni kiriting: /url <yangi_link>\nLink faqat kichik harflar, raqamlar va _ bo'lishi mumkin, 3-20 belgi.",
        'url_invalid': "Noto'g'ri link! Faqat kichik harflar, raqamlar va _ bo'lishi mumkin, 3-20 belgi.",
        'url_taken': "Bu link allaqachon band qilingan. Boshqasini tanlang.",
        'url_set': "Yangi referal link muvaffaqiyatli o'rnatildi:\n\n{ref_link}\n\nEski link endi ishlamaydi.",
        'top_users_title': "📊 TOP 30 Mashhur Foydalanuvchilar (Referrals bo'yicha):\n\n",
        'top_users_item': "{rank}. <a href=\"tg://user?id={id}\">{first_name}</a> (@{username}) ID: <code>{id}</code> Referrals: {cnt}\n",
        'unknown': "Noma'lum",
        'user_info_prompt': "Foydalanuvchi ID sini kiriting:",
        'user_not_found': "Foydalanuvchi topilmadi.",
        'user_info': "<b>Foydalanuvchi Ma'lumotlari:</b>\n\nIsm: <a href=\"tg://user?id={id}\">{first_name}</a>\nUsername: @{username}\nReferallardan ro'yxatdan o'tganlar: {referrals}\nAnonim xabarlar qabul qilgan: {messages}\nBloklaganlar soni: {blocks}\nMashhurlik reytingi: {rank} o'rin",
        'not_subscribed_alert': "Hali kanallarga obuna bo'lmagansiz!"
    },
    'en': {
        'banned': "You are banned from using the bot.",
        'subscribe_channels': "Subscribe to the following channels to use the bot: 😊",
        'own_link': "<b>This is your personal link:</b>\n\n{ref_link}\n\n<b>Share to start anonymous chat! 😊</b>",
        'self_message': "You cannot message yourself!",
        'user_banned': "This user has blocked you.",
        'invalid_link': "Error! The link may be invalid.",
        'send_message': "<b>Write your message here!</b>",
        'admin_only': "This command is for admin only!",
        'admin_panel': "Admin panel: Select actions",
        'stats': "📊 *Bot Statistics*\n\n👥 Total users: {users_count}\n🚫 Banned users: {banned_users_count}\n💬 Sent messages: {messages_count}",
        'apk_banned': "<b>Sorry, sending .apk files is prohibited!</b>",
        'new_message': "📨 You have a new anonymous message!\n\n{text}",
        'message_sent': "Your message was sent anonymously ✅",
        'use_link_first': "Please use the personal link first.",
        'reply_message': "Anonymous reply:\n\n{text}",
        'reply_sent': "Your reply was sent anonymously ✅",
        'broadcast_prompt': "Do you want to add inline buttons to the message?",
        'button_count_prompt': "Please enter a number between 1-10 for buttons.",
        'invalid_number': "Please enter a valid number (e.g., 2).",
        'button_name_prompt': "{current}-button name (total {total} buttons):",
        'button_url_prompt': "{current}-button URL:",
        'invalid_url': "Please enter a valid URL (e.g., https://example.com).",
        'broadcast_sent': "Message sent to {success} users.\nFailed: {failed}",
        'forward_sent': "Forward message sent to {success} users.\nFailed: {failed}",
        'channel_count_prompt': "Please enter a number between 1-10 for channels.",
        'channel_id_prompt': "{current}-channel ID:",
        'invalid_channel_id': "Please enter a valid channel ID (e.g., @ChannelUsername or -100123456789).",
        'channel_link_prompt': "Enter invite link for {current}-channel (e.g., https://t.me/+ABCDEF):",
        'invalid_invite_link': "Please enter a valid invite link (e.g., https://t.me/+ABCDEF).",
        'channels_set': "{count} channels set successfully.",
        'channels_removed': "Mandatory channels removed. Bot now works without subscription.",
        'thanks_subscribed': "Thanks! You can now use the bot.",
        'reply_prompt': "<b>Write your reply or send media, it will be sent anonymously!</b>",
        'message_not_found': "Error! Message not found.",
        'block': "Block",
        'block_sent': "<b>User successfully blocked. ✅</b>\n\n<blockquote>/blacklist - Clear blacklist</blockquote>",
        'unblock': "Unblock",
        'blacklist': "There are {count} users in your blacklist",
        'clear_blacklist': "Clear blacklist 🗑",
        'blacklist_cleared': "Blacklist successfully cleared.",
        'broadcast_message_prompt': "Enter the message or media to broadcast to all users:",
        'forward_message_prompt': "Send the message or media to forward:",
        'ban_usage': "Please enter user ID: /ban <user_id>",
        'banned_user': "User {user_id} banned.",
        'unban_usage': "Please enter user ID: /unban <user_id>",
        'unbanned_user': "User unbanned.",
        'not_banned': "User was not banned.",
        'warn_usage': "Please enter user ID: /warn <user_id>",
        'warned_user': "Warning sent to user {user_id}.",
        'error_id': "Error! ID may be invalid.",
        'warn_message': "<b>Warning! A complaint was filed against you, repeat may lead to ban!</b>",
        'lang_prompt': "Select the language for the bot",
        'mystats': "<b>📌 Profile Statistics</b>\n\n<b>Today:</b>\n<blockquote>💬 Messages: {today_messages}\n👀 Link visits: {today_referrals}\n⭐️ Popularity: {popularity_rank} place</blockquote>\n\n<b>Total:</b>\n<blockquote>💬 Messages: {total_messages}\n👀 Link visits: {total_referrals}\n⭐️ Popularity: {popularity_rank} place</blockquote>\n\n⭐️ To increase popularity, share your personal link:\n👉 {ref_link}",
        'share_button': "Share",
        'share_post': "You can send me an anonymous message via this link😊\n\n👉🏻 {ref_link}",
        'media_error': "Error sending media, but text sent:\n\n",
        'url_usage': "Please enter new link: /url <new_link>\nLink can only contain lowercase letters, numbers and _, 3-20 characters.",
        'url_invalid': "Invalid link! Only lowercase letters, numbers and _ allowed, 3-20 characters.",
        'url_taken': "This link is already taken. Choose another.",
        'url_set': "New referral link set successfully:\n\n{ref_link}\n\nOld link no longer works.",
        'top_users_title': "📊 TOP 30 Popular Users (by Referrals):\n\n",
        'top_users_item': "{rank}. <a href=\"tg://user?id={id}\">{first_name}</a> (@{username}) ID: <code>{id}</code> Referrals: {cnt}\n",
        'unknown': "Unknown",
        'user_info_prompt': "Enter user ID:",
        'user_not_found': "User not found.",
        'user_info': "<b>User Info:</b>\n\nName: <a href=\"tg://user?id={id}\">{first_name}</a>\nUsername: @{username}\nReferrals registered: {referrals}\nAnonymous messages received: {messages}\nBlocked count: {blocks}\nPopularity rank: {rank}",
        'not_subscribed_alert': "You haven't subscribed to the channels yet!"
    },
    'ru': {
        'banned': "Вы заблокированы в боте.",
        'subscribe_channels': "Подпишитесь на следующие каналы, чтобы использовать бота: 😊",
        'own_link': "<b>Это ваша личная ссылка:</b>\n\n{ref_link}\n\n<b>Поделитесь для анонимного чата! 😊</b>",
        'self_message': "Вы не можете отправить сообщение себе!",
        'user_banned': "Этот пользователь вас заблокировал.",
        'invalid_link': "Ошибка! Ссылка может быть недействительной.",
        'send_message': "<b>Напишите ваше сообщение здесь!</b>",
        'admin_only': "Эта команда только для админа!",
        'admin_panel': "Панель админа: Выберите действия",
        'stats': "📊 *Статистика бота*\n\n👥 Всего пользователей: {users_count}\n🚫 Заблокированных пользователей: {banned_users_count}\n💬 Отправленных сообщений: {messages_count}",
        'apk_banned': "<b>Извините, отправка .apk файлов запрещена!</b>",
        'new_message': "📨 У вас новое анонимное сообщение!\n\n{text}",
        'message_sent': "Ваше сообщение отправлено анонимно ✅",
        'use_link_first': "Пожалуйста, сначала используйте личную ссылку.",
        'reply_message': "Анонимный ответ:\n\n{text}",
        'reply_sent': "Ваш ответ отправлен анонимно ✅",
        'broadcast_prompt': "Хотите добавить inline-кнопки к сообщению?",
        'button_count_prompt': "Пожалуйста, введите число от 1 до 10 для кнопок.",
        'invalid_number': "Пожалуйста, введите правильное число (например, 2).",
        'button_name_prompt': "{current}-кнопка имя (всего {total} кнопок):",
        'button_url_prompt': "{current}-кнопка URL:",
        'invalid_url': "Пожалуйста, введите правильный URL (например, https://example.com).",
        'broadcast_sent': "Сообщение отправлено {success} пользователям.\nНеудачно: {failed}",
        'forward_sent': "Пересланное сообщение отправлено {success} пользователям.\nНеудачно: {failed}",
        'channel_count_prompt': "Пожалуйста, введите число от 1 до 10 для каналов.",
        'channel_id_prompt': "{current}-канал ID:",
        'invalid_channel_id': "Пожалуйста, введите правильный ID канала (например, @ChannelUsername или -100123456789).",
        'channel_link_prompt': "Введите invite-ссылку для {current}-канала (например, https://t.me/+ABCDEF):",
        'invalid_invite_link': "Пожалуйста, введите правильную invite-ссылку (например, https://t.me/+ABCDEF).",
        'channels_set': "{count} каналов установлено успешно.",
        'channels_removed': "Обязательные каналы удалены. Бот теперь работает без подписки.",
        'thanks_subscribed': "Спасибо! Теперь вы можете использовать бота.",
        'reply_prompt': "<b>Напишите ваш ответ или отправьте медиа, оно будет отправлено анонимно!</b>",
        'message_not_found': "Ошибка! Сообщение не найдено.",
        'block': "Заблокировать",
        'block_sent': "<b>Пользователь успешно заблокирован. ✅</b>\n\n<blockquote>/blacklist - Очистить черный список</blockquote>",
        'unblock': "Разблокировать",
        'blacklist': "В вашем черном списке {count} пользователей",
        'clear_blacklist': "Очистить черный список 🗑",
        'blacklist_cleared': "Черный список успешно очищен.",
        'broadcast_message_prompt': "Введите сообщение или медиа для рассылки всем пользователям:",
        'forward_message_prompt': "Отправьте сообщение или медиа для пересылки:",
        'ban_usage': "Пожалуйста, введите ID пользователя: /ban <user_id>",
        'banned_user': "Пользователь {user_id} заблокирован.",
        'unban_usage': "Пожалуйста, введите ID пользователя: /unban <user_id>",
        'unbanned_user': "Пользователь разблокирован.",
        'not_banned': "Пользователь не был заблокирован.",
        'warn_usage': "Пожалуйста, введите ID пользователя: /warn <user_id>",
        'warned_user': "Предупреждение отправлено пользователю {user_id}.",
        'error_id': "Ошибка! ID может быть недействительным.",
        'warn_message': "<b>Предупреждение! На вас поступила жалоба, повторение может привести к бану!</b>",
        'lang_prompt': "Выберите язык для бота",
        'mystats': "<b>📌 Статистика профиля</b>\n\n<b>Сегодня:</b>\n<blockquote>💬 Сообщения: {today_messages}\n👀 Посещения по ссылке: {today_referrals}\n⭐️ Популярность: {popularity_rank} место</blockquote>\n\n<b>Всего:</b>\n<blockquote>💬 Сообщения: {total_messages}\n👀 Посещения по ссылке: {total_referrals}\n⭐️ Популярность: {popularity_rank} место</blockquote>\n\n⭐️ Чтобы повысить популярность, распространяйте свою личную ссылку:\n👉 {ref_link}",
        'share_button': "Поделиться",
        'share_post': "Вы можете отправить мне анонимное сообщение по этой ссылке😊\n\n👉🏻 {ref_link}",
        'media_error': "Ошибка отправки медиа, но текст отправлен:\n\n",
        'url_usage': "Пожалуйста, введите новый ссылку: /url <new_link>\nСсылка может содержать только строчные буквы, цифры и _, 3-20 символов.",
        'url_invalid': "Недействительная ссылка! Только строчные буквы, цифры и _ разрешены, 3-20 символов.",
        'url_taken': "Эта ссылка уже занята. Выберите другую.",
        'url_set': "Новая реферальная ссылка успешно установлена:\n\n{ref_link}\n\nСтарая ссылка больше не работает.",
        'top_users_title': "📊 TOP 30 Популярных Пользователей (по Рефералам):\n\n",
        'top_users_item': "{rank}. <a href=\"tg://user?id={id}\">{first_name}</a> (@{username}) ID: <code>{id}</code> Рефералы: {cnt}\n",
        'unknown': "Неизвестно",
        'user_info_prompt': "Введите ID пользователя:",
        'user_not_found': "Пользователь не найден.",
        'user_info': "<b>Информация о Пользователе:</b>\n\nИмя: <a href=\"tg://user?id={id}\">{first_name}</a>\nUsername: @{username}\nЗарегистрировано по рефералам: {referrals}\nПолучено анонимных сообщений: {messages}\nЗаблокировано: {blocks}\nРанг популярности: {rank}",
        'not_subscribed_alert': "Вы еще не подписаны на каналы!"
    }
}

def get_translation(lang, key, **kwargs):
    text = translations.get(lang, translations['uz']).get(key, '')
    return text.format(**kwargs)

async def send_media_message(bot, chat_id, media_type, file_id, caption, text, reply_markup=None, entities=None, poll_data=None, lang='uz'):
    try:
        caption_entities = [MessageEntity(**entity) for entity in (entities or [])] if entities else None
        if media_type == 'photo':
            await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption, reply_markup=reply_markup, caption_entities=caption_entities, parse_mode=None)  # parse_mode olib tashlandi
        elif media_type == 'video':
            await bot.send_video(chat_id=chat_id, video=file_id, caption=caption, reply_markup=reply_markup, caption_entities=caption_entities, parse_mode=None)
        elif media_type == 'document':
            await bot.send_document(chat_id=chat_id, document=file_id, caption=caption, reply_markup=reply_markup, caption_entities=caption_entities, parse_mode=None)
        elif media_type == 'sticker':
            await bot.send_sticker(chat_id=chat_id, sticker=file_id, reply_markup=reply_markup)
        elif media_type == 'audio':
            await bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption, reply_markup=reply_markup, caption_entities=caption_entities, parse_mode=None)
        elif media_type == 'animation':
            await bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption, reply_markup=reply_markup, caption_entities=caption_entities, parse_mode=None)
        elif media_type == 'voice':
            await bot.send_voice(chat_id=chat_id, voice=file_id, caption=caption, reply_markup=reply_markup, caption_entities=caption_entities, parse_mode=None)
        elif media_type == 'poll':
            await bot.send_poll(
                chat_id=chat_id,
                question=poll_data['question'],
                options=poll_data['options'],
                is_anonymous=poll_data.get('is_anonymous', True),
                allows_multiple_answers=poll_data.get('allows_multiple_answers', False),
                type=poll_data.get('type', Poll.REGULAR),
                reply_markup=reply_markup
            )
        else:
            entities_list = [MessageEntity(**entity) for entity in (entities or [])] if entities else None
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, entities=entities_list, parse_mode=None)  # parse_mode olib tashlandi
    except Exception as e:
        print(f"Media yuborishda xato: {e}")
        await bot.send_message(chat_id=chat_id, text=get_translation(lang, 'media_error') + text, parse_mode=None)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    if is_user_banned(user_id):
        await update.message.reply_text(get_translation(lang, 'banned'))
        return

    if not await check_channel_membership(user_id, context):
        reply_markup = await get_channels_keyboard(lang)
        await update.message.reply_text(get_translation(lang, 'subscribe_channels'), reply_markup=reply_markup)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                           (user_id, "pending_membership", json.dumps({"args": context.args})))
            conn.commit()
        return

    add_user_to_db(user_id, lang, first_name, username)
    args = context.args
    if not args:
        ref_link = get_ref_link(user_id)
        await update.message.reply_text(get_translation(lang, 'own_link', ref_link=ref_link), parse_mode="HTML")
    else:
        try:
            receiver_id = get_user_from_ref(args[0])
            if receiver_id == user_id:
                await update.message.reply_text(get_translation(lang, 'self_message'))
                return
            if is_user_banned(receiver_id):
                await update.message.reply_text(get_translation(lang, 'user_banned'))
                return
            # Track referral
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (receiver_id, user_id))
                conn.commit()
            add_user_to_db(user_id, lang, first_name, username)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                               (user_id, "send", str(receiver_id)))
                conn.commit()
            await update.message.reply_text(get_translation(lang, 'send_message'), parse_mode="HTML")
        except ValueError:
            await update.message.reply_text(get_translation(lang, 'invalid_link'))

async def url_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    if is_user_banned(user_id):
        await update.message.reply_text(get_translation(lang, 'banned'))
        return

    args = context.args
    if not args:
        await update.message.reply_text(get_translation(lang, 'url_usage'))
        return

    new_ref = args[0].lower()
    if not is_valid_custom_ref(new_ref):
        await update.message.reply_text(get_translation(lang, 'url_invalid'))
        return

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE custom_ref = ?", (new_ref,))
        if cursor.fetchone()[0] > 0:
            await update.message.reply_text(get_translation(lang, 'url_taken'))
            return
        cursor.execute("UPDATE users SET custom_ref = ? WHERE id = ?", (new_ref, user_id))
        conn.commit()

    ref_link = get_ref_link(user_id)
    await update.message.reply_text(get_translation(lang, 'url_set', ref_link=ref_link), parse_mode="HTML")

async def blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    count = get_blacklist_count(user_id)
    keyboard = [[InlineKeyboardButton(get_translation(lang, 'clear_blacklist'), callback_data="clear_blacklist")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(get_translation(lang, 'blacklist', count=count), reply_markup=reply_markup, parse_mode="HTML")

async def lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    keyboard = [
        [InlineKeyboardButton("🇺🇿 O'zbek", callback_data="lang_uz"),
         InlineKeyboardButton("🇺🇸 English", callback_data="lang_en"),
         InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(get_translation(lang, 'lang_prompt'), reply_markup=reply_markup)

async def mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    today = datetime.now().date().isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Today messages received
        cursor.execute("SELECT COUNT(*) FROM messages WHERE receiver_id = ? AND DATE(timestamp) = ?", (user_id, today))
        today_messages = cursor.fetchone()[0]
        # Total messages received
        cursor.execute("SELECT COUNT(*) FROM messages WHERE receiver_id = ?", (user_id,))
        total_messages = cursor.fetchone()[0]
        # Today referrals (link visits)
        cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND DATE(timestamp) = ?", (user_id, today))
        today_referrals = cursor.fetchone()[0]
        # Total referrals
        cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
        total_referrals = cursor.fetchone()[0]
        # Popularity rank based on referrals
        cursor.execute("""
            SELECT u.id, COUNT(r.referred_id) as cnt
            FROM users u LEFT JOIN referrals r ON u.id = r.referrer_id
            GROUP BY u.id
            ORDER BY cnt DESC
        """)
        ranks = cursor.fetchall()
        rank_dict = {row['id']: i+1 for i, row in enumerate(ranks)}
        popularity_rank = rank_dict.get(user_id, len(ranks) + 1)

    ref_link = get_ref_link(user_id)
    stats_text = get_translation(lang, 'mystats', today_messages=today_messages, today_referrals=today_referrals,
                                 popularity_rank=popularity_rank, total_messages=total_messages,
                                 total_referrals=total_referrals, ref_link=ref_link)
    # Share button using t.me/share/url
    share_text = get_translation(lang, 'share_post', ref_link=ref_link).rsplit('👉🏻', 1)[0].strip()  # Remove the link part from text
    share_url = f"https://t.me/share/url?url={ref_link}&text={urllib.parse.quote(share_text)}"
    keyboard = [[InlineKeyboardButton(get_translation(lang, 'share_button'), url=share_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(stats_text, reply_markup=reply_markup, parse_mode="HTML")

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    if not is_admin(user_id):
        await update.message.reply_text(get_translation(lang, 'admin_only'))
        return
    keyboard = [
        [InlineKeyboardButton("Barchaga xabar yuborish" if lang == 'uz' else "Broadcast to all" if lang == 'en' else "Рассылка всем", callback_data="broadcast")],
        [InlineKeyboardButton("Forward qilish" if lang == 'uz' else "Forward" if lang == 'en' else "Переслать", callback_data="forward")],
        [InlineKeyboardButton("Kanalga aʼzo qilish" if lang == 'uz' else "Set channels" if lang == 'en' else "Установить каналы", callback_data="set_channel")],
        [InlineKeyboardButton("Kanalni o‘chirish" if lang == 'uz' else "Remove channels" if lang == 'en' else "Удалить каналы", callback_data="remove_channel")],
        [InlineKeyboardButton("TOP 30 Mashhurlar" if lang == 'uz' else "TOP 30 Popular" if lang == 'en' else "ТОП 30 Популярных", callback_data="top_users")],
        [InlineKeyboardButton("Foydalanuvchi Ma'lumotlari" if lang == 'uz' else "User Info" if lang == 'en' else "Инфо Пользователя", callback_data="user_info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(get_translation(lang, 'admin_panel'), reply_markup=reply_markup)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    if not is_admin(user_id):
        await update.message.reply_text(get_translation(lang, 'admin_only'))
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        users_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM banned_users")
        banned_users_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM messages")
        messages_count = cursor.fetchone()[0]
    stats_text = get_translation(lang, 'stats', users_count=users_count, banned_users_count=banned_users_count, messages_count=messages_count)
    await update.message.reply_text(stats_text, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    lang = get_user_language(user_id)
    if is_user_banned(user_id):
        await update.message.reply_text(get_translation(lang, 'banned'))
        return

    if not await check_channel_membership(user_id, context):
        reply_markup = await get_channels_keyboard(lang)
        await update.message.reply_text(get_translation(lang, 'subscribe_channels'), reply_markup=reply_markup)
        return

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT step, data FROM sessions WHERE user_id = ?", (user_id,))
        session = cursor.fetchone()

    reply_to = update.message.reply_to_message
    if reply_to and reply_to.from_user.id == context.bot.id and reply_to.reply_markup and reply_to.reply_markup.inline_keyboard:
        # Check if it's an anonymous message
        keyboard = reply_to.reply_markup.inline_keyboard
        if keyboard and keyboard[0] and keyboard[0][0].callback_data.startswith("block_"):
            message_id = keyboard[0][0].callback_data.split("_", 1)[1]
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT sender_id FROM messages WHERE message_id = ?", (message_id,))
                message = cursor.fetchone()
                if message:
                    # Set session to reply mode
                    cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                                   (user_id, "reply", str(message["sender_id"])))
                    conn.commit()
                    session = {"step": "reply", "data": str(message["sender_id"])}

    if not session:
        await update.message.reply_text(get_translation(lang, 'use_link_first'))
        return

    step, data = session["step"], session["data"]

    media_type = 'text'
    file_id = None
    caption = update.message.caption or ''
    text = update.message.text or caption or "Media fayl"
    entities_source = update.message.entities if media_type == 'text' else update.message.caption_entities
    entities = [serialize_entity(entity) for entity in (entities_source or [])]
    poll_data = None

    if update.message.photo:
        media_type = 'photo'
        file_id = update.message.photo[-1].file_id
    elif update.message.video:
        media_type = 'video'
        file_id = update.message.video.file_id
    elif update.message.document:
        media_type = 'document'
        file_id = update.message.document.file_id
        if update.message.document.file_name and update.message.document.file_name.lower().endswith('.apk'):
            await update.message.reply_text(get_translation(lang, 'apk_banned'), parse_mode="HTML")
            return
    elif update.message.sticker:
        media_type = 'sticker'
        file_id = update.message.sticker.file_id
        caption = ''  # Stickerda caption yo'q
    elif update.message.audio:
        media_type = 'audio'
        file_id = update.message.audio.file_id
    elif update.message.animation:
        media_type = 'animation'
        file_id = update.message.animation.file_id
    elif update.message.voice:
        media_type = 'voice'
        file_id = update.message.voice.file_id
    elif update.message.poll:
        media_type = 'poll'
        poll_data = serialize_poll(update.message.poll)
        text = update.message.poll.question  # For consistency

    if step == "send":
        receiver_id = int(data)
        if is_user_blocked(receiver_id, user_id):
            await update.message.reply_text(get_translation(lang, 'user_banned'))
            return
        message_id = f"{user_id}_{receiver_id}_{update.message.message_id}"
        try:
            receiver_chat = await context.bot.get_chat(receiver_id)
            receiver_name = receiver_chat.first_name or "Unknown"
            receiver_username = receiver_chat.username or "Unknown"
            update_user_info(receiver_id, receiver_name, receiver_username)
        except Exception:
            receiver_name = receiver_username = "Unknown"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''INSERT INTO messages (message_id, sender_id, receiver_id, text, media_type, file_id, caption, sender_name, sender_username, receiver_name, receiver_username)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                           (message_id, user_id, receiver_id, text, media_type, file_id, caption,
                            update.effective_user.first_name or "Unknown", update.effective_user.username or "Unknown",
                            receiver_name, receiver_username))
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()

        receiver_lang = get_user_language(receiver_id)
        keyboard = [
            [InlineKeyboardButton(get_translation(receiver_lang, 'block'), callback_data=f"block_{message_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if media_type == 'text':
            entities_list = [MessageEntity(**entity) for entity in entities]
            await context.bot.send_message(chat_id=receiver_id, text=get_translation(receiver_lang, 'new_message', text=text), reply_markup=reply_markup, entities=entities_list, parse_mode=None)
        else:
            await send_media_message(context.bot, receiver_id, media_type, file_id, caption, get_translation(receiver_lang, 'new_message', text=text), reply_markup, entities, poll_data, receiver_lang)

        await update.message.reply_text(get_translation(lang, 'message_sent'))
        ref_link = get_ref_link(user_id)
        await update.message.reply_text(get_translation(lang, 'own_link', ref_link=ref_link), parse_mode="HTML")

    elif step == "reply":
        original_sender_id = int(data)
        sender_lang = get_user_language(original_sender_id)
        if media_type == 'text':
            entities_list = [MessageEntity(**entity) for entity in entities]
            await context.bot.send_message(chat_id=original_sender_id, text=get_translation(sender_lang, 'reply_message', text=text), entities=entities_list, parse_mode=None)
        else:
            await send_media_message(context.bot, original_sender_id, media_type, file_id, caption, get_translation(sender_lang, 'reply_message', text=text), None, entities, poll_data, sender_lang)
        await update.message.reply_text(get_translation(lang, 'reply_sent'))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()

    elif step == "broadcast_message":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        broadcast_data = {
            "media_type": media_type,
            "file_id": file_id,
            "caption": caption,
            "message": text,
            "entities": entities
        }
        if media_type == 'poll':
            broadcast_data["poll_data"] = poll_data
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                           (user_id, "broadcast_ask_inline", json.dumps(broadcast_data)))
            conn.commit()
        yes_text = "Ha" if lang == 'uz' else "Yes" if lang == 'en' else "Да"
        no_text = "Yo‘q" if lang == 'uz' else "No" if lang == 'en' else "Нет"
        keyboard = [
            [InlineKeyboardButton(yes_text, callback_data="broadcast_add_buttons")],
            [InlineKeyboardButton(no_text, callback_data="broadcast_no_buttons")]
        ]
        await update.message.reply_text(get_translation(lang, 'broadcast_prompt'), reply_markup=InlineKeyboardMarkup(keyboard))

    elif step == "broadcast_ask_count":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        try:
            button_count = int(text)
            if button_count <= 0 or button_count > 10:
                await update.message.reply_text(get_translation(lang, 'button_count_prompt'))
                return
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,))
                session_data = json.loads(cursor.fetchone()["data"])
                session_data["count"] = button_count
                session_data["names"] = []
                session_data["urls"] = []
                cursor.execute("UPDATE sessions SET step = ?, data = ? WHERE user_id = ?",
                               ("broadcast_ask_button_name", json.dumps(session_data), user_id))
                conn.commit()
            await update.message.reply_text(get_translation(lang, 'button_name_prompt', current=1, total=button_count))
        except ValueError:
            await update.message.reply_text(get_translation(lang, 'invalid_number'))

    elif step == "broadcast_ask_button_name":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,))
            session_data = json.loads(cursor.fetchone()["data"])
            session_data["names"].append(text)
            if len(session_data["names"]) < session_data["count"]:
                cursor.execute("UPDATE sessions SET data = ? WHERE user_id = ?", (json.dumps(session_data), user_id))
                conn.commit()
                await update.message.reply_text(get_translation(lang, 'button_name_prompt', current=len(session_data["names"])+1, total=session_data["count"]))
            else:
                cursor.execute("UPDATE sessions SET step = ?, data = ? WHERE user_id = ?",
                               ("broadcast_ask_button_url", json.dumps(session_data), user_id))
                conn.commit()
                await update.message.reply_text(get_translation(lang, 'button_url_prompt', current=1))

    elif step == "broadcast_ask_button_url":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        url = text
        if not is_valid_url(url):
            await update.message.reply_text(get_translation(lang, 'invalid_url'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,))
            session_data = json.loads(cursor.fetchone()["data"])
            session_data["urls"].append(url)
            if len(session_data["urls"]) < session_data["count"]:
                cursor.execute("UPDATE sessions SET data = ? WHERE user_id = ?", (json.dumps(session_data), user_id))
                conn.commit()
                await update.message.reply_text(get_translation(lang, 'button_url_prompt', current=len(session_data["urls"])+1))
            else:
                keyboard = [[InlineKeyboardButton(name, url=u)] for name, u in zip(session_data["names"], session_data["urls"])]
                reply_markup = InlineKeyboardMarkup(keyboard)
                cursor.execute("SELECT id FROM users")
                users = [row["id"] for row in cursor.fetchall()]
                cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                conn.commit()
                success_count = failed_count = 0
                for target_id in users:
                    if not is_user_banned(target_id) and target_id != user_id:
                        target_lang = get_user_language(target_id)
                        try:
                            if session_data["media_type"] == 'text':
                                entities_list = [MessageEntity(**entity) for entity in session_data["entities"]]
                                await context.bot.send_message(
                                    chat_id=target_id,
                                    text=session_data["message"],
                                    entities=entities_list,
                                    reply_markup=reply_markup,
                                    parse_mode=None  # parse_mode olib tashlandi
                                )
                            elif session_data["media_type"] == 'poll':
                                await send_media_message(context.bot, target_id, session_data["media_type"], None, None, None, reply_markup, None, session_data.get("poll_data"), target_lang)
                            else:
                                await send_media_message(context.bot, target_id, session_data["media_type"], session_data["file_id"], session_data["caption"], session_data["message"], reply_markup, session_data["entities"], None, target_lang)
                            success_count += 1
                        except Exception:
                            failed_count += 1
                await update.message.reply_text(get_translation(lang, 'broadcast_sent', success=success_count, failed=failed_count))

    elif step == "forward_message":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users")
            users = [row["id"] for row in cursor.fetchall()]
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        success_count = failed_count = 0
        for target_id in users:
            if not is_user_banned(target_id) and target_id != user_id:
                try:
                    await update.message.forward(chat_id=target_id)
                    success_count += 1
                except Exception:
                    failed_count += 1
        await update.message.reply_text(get_translation(lang, 'forward_sent', success=success_count, failed=failed_count))

    elif step == "set_channel_count":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        try:
            channel_count = int(text)
            if channel_count <= 0 or channel_count > 10:
                await update.message.reply_text(get_translation(lang, 'channel_count_prompt'))
                return
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                               (user_id, "set_channel_id", json.dumps({"count": channel_count, "channels": [], "current_channel": 1})))
                conn.commit()
            await update.message.reply_text(get_translation(lang, 'channel_id_prompt', current=1))
        except ValueError:
            await update.message.reply_text(get_translation(lang, 'invalid_number'))

    elif step == "set_channel_id":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        input_str = text.strip()
        if not is_valid_channel_id(input_str):
            await update.message.reply_text(get_translation(lang, 'invalid_channel_id'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,))
            session_data = json.loads(cursor.fetchone()["data"])
            session_data["channels"].append({"id": input_str, "name": "Join", "link": ""})
            cursor.execute("UPDATE sessions SET step = ?, data = ? WHERE user_id = ?",
                           ("set_channel_link", json.dumps(session_data), user_id))
            conn.commit()
        await update.message.reply_text(get_translation(lang, 'channel_link_prompt', current=session_data['current_channel']))

    elif step == "set_channel_link":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        invite_link = text.strip()
        if not is_valid_invite_link(invite_link):
            await update.message.reply_text(get_translation(lang, 'invalid_invite_link'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,))
            session_data = json.loads(cursor.fetchone()["data"])
            session_data["channels"][-1]["link"] = invite_link
            if len(session_data["channels"]) < session_data["count"]:
                session_data["current_channel"] += 1
                cursor.execute("UPDATE sessions SET step = ?, data = ? WHERE user_id = ?",
                               ("set_channel_id", json.dumps(session_data), user_id))
                conn.commit()
                await update.message.reply_text(get_translation(lang, 'channel_id_prompt', current=session_data['current_channel']))
            else:
                cursor.execute("DELETE FROM channels")
                for channel in session_data["channels"]:
                    cursor.execute("INSERT INTO channels (id, link, name) VALUES (?, ?, ?)",
                                   (channel["id"], channel["link"], channel["name"]))
                cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                conn.commit()
                await update.message.reply_text(get_translation(lang, 'channels_set', count=session_data['count']))

    elif step == "get_user_id":
        if not is_admin(user_id):
            await update.message.reply_text(get_translation(lang, 'admin_only'))
            return
        try:
            target_id = int(text)
        except ValueError:
            await update.message.reply_text(get_translation(lang, 'error_id'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT first_name, username FROM users WHERE id = ?", (target_id,))
            user = cursor.fetchone()
            if not user:
                await update.message.reply_text(get_translation(lang, 'user_not_found'))
                cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                conn.commit()
                return
            first_name = html.escape(user['first_name'] or get_translation(lang, 'unknown'))
            username = html.escape(user['username'] or get_translation(lang, 'unknown'))
            cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (target_id,))
            referrals = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM messages WHERE receiver_id = ?", (target_id,))
            messages = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM user_blacklists WHERE blocker_id = ?", (target_id,))
            blocks = cursor.fetchone()[0]
            cursor.execute("""
                SELECT u.id, COUNT(r.referred_id) as cnt
                FROM users u LEFT JOIN referrals r ON u.id = r.referrer_id
                GROUP BY u.id
                ORDER BY cnt DESC
            """)
            ranks = cursor.fetchall()
            rank_dict = {row['id']: i+1 for i, row in enumerate(ranks)}
            rank = rank_dict.get(target_id, len(ranks) + 1)
            info_text = get_translation(lang, 'user_info', id=target_id, first_name=first_name, username=username, referrals=referrals, messages=messages, blocks=blocks, rank=rank)
            await update.message.reply_text(info_text, parse_mode="HTML")
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    first_name = query.from_user.first_name
    username = query.from_user.username
    update_user_info(user_id, first_name, username)
    lang = get_user_language(user_id)
    if is_user_banned(user_id):
        await query.message.reply_text(get_translation(lang, 'banned'))
        return

    data = query.data
    try:
        await query.answer()
    except telegram.error.BadRequest as e:
        if "query is too old" in str(e).lower() or "query id is invalid" in str(e).lower():
            pass  # Continue without answering
        else:
            raise

    if data.startswith("lang_"):
        new_lang = data.split("_")[1]
        update_user_language(user_id, new_lang)
        await query.message.edit_text(f"Til {new_lang.upper()} ga o'zgartirildi." if lang == 'uz' else f"Language set to {new_lang.upper()}" if lang == 'en' else f"Язык установлен на {new_lang.upper()}")
        return

    if data == "check_membership":
        if await check_channel_membership(user_id, context):
            await query.message.delete()
            await context.bot.send_message(chat_id=user_id, text=get_translation(lang, 'thanks_subscribed'))
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,))
                session = cursor.fetchone()
                if session:
                    args = json.loads(session["data"]).get("args", [])
                    cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
                    conn.commit()
                    if args:
                        try:
                            receiver_id = get_user_from_ref(args[0])
                            if receiver_id == user_id:
                                await context.bot.send_message(chat_id=user_id, text=get_translation(lang, 'self_message'))
                                return
                            if is_user_banned(receiver_id):
                                await context.bot.send_message(chat_id=user_id, text=get_translation(lang, 'user_banned'))
                                return
                            # Track referral again if needed
                            with get_db_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (receiver_id, user_id))
                                conn.commit()
                            add_user_to_db(user_id, lang, first_name, username)
                            with get_db_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                                               (user_id, "send", str(receiver_id)))
                                conn.commit()
                            await context.bot.send_message(chat_id=user_id, text=get_translation(lang, 'send_message'), parse_mode="HTML")
                        except ValueError:
                            await context.bot.send_message(chat_id=user_id, text=get_translation(lang, 'invalid_link'))
                    else:
                        ref_link = get_ref_link(user_id)
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=get_translation(lang, 'own_link', ref_link=ref_link),
                            parse_mode="HTML"
                        )
        else:
            await query.answer(get_translation(lang, 'not_subscribed_alert'), show_alert=True)

    elif data.startswith("block_"):
        message_id = data.split("_", 1)[1]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,))
            message = cursor.fetchone()
        if message:
            block_user(user_id, message["sender_id"])
            report_lang = get_user_language(ADMIN_ID)
            report_text = (
                f"📢 *{get_translation(report_lang, 'block')}*\n\n"
                f"👤 *Bloklovchi*:\n  Ism: [{message['receiver_name']}](tg://user?id={message['receiver_id']})\n"
                f"  Username: @{message['receiver_username']}\n  ID: `{message['receiver_id']}`\n\n"
                f"👤 *Bloklangan*:\n  Ism: [{message['sender_name']}](tg://user?id={message['sender_id']})\n"
                f"  Username: @{message['sender_username']}\n  ID: `{message['sender_id']}`\n\n"
                f"📜 *Xabar*:\n{message['text']}\n"
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=report_text, parse_mode="Markdown")
            if message['media_type'] != 'text':
                await send_media_message(context.bot, ADMIN_ID, message['media_type'], message['file_id'], message['caption'], message['text'], lang=report_lang)
            keyboard = [[InlineKeyboardButton(get_translation(lang, 'unblock'), callback_data=f"unblock_{message['sender_id']}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text(get_translation(lang, 'block_sent'), reply_markup=reply_markup, parse_mode="HTML")
        else:
            await query.message.reply_text(get_translation(lang, 'message_not_found'))

    elif data.startswith("unblock_"):
        blocked_id = int(data.split("_", 1)[1])
        if unblock_user(user_id, blocked_id):
            await query.message.reply_text(get_translation(lang, 'unbanned_user'), parse_mode="HTML")
        else:
            await query.message.reply_text(get_translation(lang, 'not_banned'), parse_mode="HTML")

    elif data == "clear_blacklist":
        count = clear_blacklist(user_id)
        await query.message.reply_text(get_translation(lang, 'blacklist_cleared'), parse_mode="HTML")

    elif data == "broadcast":
        if not is_admin(user_id):
            await query.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                           (user_id, "broadcast_message", json.dumps({})))
            conn.commit()
        await query.message.reply_text(get_translation(lang, 'broadcast_message_prompt'))

    elif data == "forward":
        if not is_admin(user_id):
            await query.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                           (user_id, "forward_message", json.dumps({})))
            conn.commit()
        await query.message.reply_text(get_translation(lang, 'forward_message_prompt'))

    elif data == "broadcast_add_buttons":
        if not is_admin(user_id):
            await query.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE sessions SET step = ? WHERE user_id = ?", ("broadcast_ask_count", user_id))
            conn.commit()
        await query.message.reply_text(get_translation(lang, 'button_count_prompt'))

    elif data == "broadcast_no_buttons":
        if not is_admin(user_id):
            await query.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM sessions WHERE user_id = ?", (user_id,))
            session_data = json.loads(cursor.fetchone()["data"])
            cursor.execute("SELECT id FROM users")
            users = [row["id"] for row in cursor.fetchall()]
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
        success_count = failed_count = 0
        for target_id in users:
            if not is_user_banned(target_id) and target_id != user_id:
                target_lang = get_user_language(target_id)
                try:
                    if session_data["media_type"] == 'text':
                        entities_list = [MessageEntity(**entity) for entity in session_data["entities"]]
                        await context.bot.send_message(
                            chat_id=target_id,
                            text=session_data["message"],
                            entities=entities_list,
                            parse_mode=None  # parse_mode olib tashlandi
                        )
                    elif session_data["media_type"] == 'poll':
                        await send_media_message(context.bot, target_id, session_data["media_type"], None, None, None, None, None, session_data.get("poll_data"), target_lang)
                    else:
                        await send_media_message(context.bot, target_id, session_data["media_type"], session_data["file_id"], session_data["caption"], session_data["message"], None, session_data["entities"], None, target_lang)
                    success_count += 1
                except Exception:
                    failed_count += 1
        await query.message.reply_text(get_translation(lang, 'broadcast_sent', success=success_count, failed=failed_count))

    elif data == "set_channel":
        if not is_admin(user_id):
            await query.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                           (user_id, "set_channel_count", json.dumps({})))
            conn.commit()
        await query.message.reply_text(get_translation(lang, 'channel_count_prompt'))

    elif data == "remove_channel":
        if not is_admin(user_id):
            await query.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM channels")
            conn.commit()
        await query.message.reply_text(get_translation(lang, 'channels_removed'))

    elif data == "top_users":
        if not is_admin(user_id):
            await query.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT u.id, u.first_name, u.username, COUNT(r.referred_id) as cnt
                FROM users u LEFT JOIN referrals r ON u.id = r.referrer_id
                GROUP BY u.id
                ORDER BY cnt DESC
                LIMIT 30
            """)
            top_users = cursor.fetchall()
        top_text = get_translation(lang, 'top_users_title')
        for i, user in enumerate(top_users, 1):
            first_name = html.escape(user['first_name'] or get_translation(lang, 'unknown'))
            username = html.escape(user['username'] or get_translation(lang, 'unknown'))
            top_text += get_translation(lang, 'top_users_item', rank=i, first_name=first_name, id=user['id'], username=username, cnt=user['cnt'])
        await query.message.reply_text(top_text, parse_mode="HTML")

    elif data == "user_info":
        if not is_admin(user_id):
            await query.message.reply_text(get_translation(lang, 'admin_only'))
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO sessions (user_id, step, data) VALUES (?, ?, ?)",
                           (user_id, "get_user_id", json.dumps({})))
            conn.commit()
        await query.message.reply_text(get_translation(lang, 'user_info_prompt'))

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    if not is_admin(user_id):
        await update.message.reply_text(get_translation(lang, 'admin_only'))
        return
    args = context.args
    if not args:
        await update.message.reply_text(get_translation(lang, 'ban_usage'))
        return
    try:
        ban_id = int(args[0])
        ban_user(ban_id)
        ban_lang = get_user_language(ban_id)
        await context.bot.send_message(chat_id=ban_id, text=get_translation(ban_lang, 'banned'))
        await update.message.reply_text(get_translation(lang, 'banned_user', user_id=ban_id))
    except Exception:
        await update.message.reply_text(get_translation(lang, 'error_id'))

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    if not is_admin(user_id):
        await update.message.reply_text(get_translation(lang, 'admin_only'))
        return
    args = context.args
    if not args:
        await update.message.reply_text(get_translation(lang, 'unban_usage'))
        return
    try:
        unban_id = int(args[0])
        if unban_user(unban_id):
            unban_lang = get_user_language(unban_id)
            await context.bot.send_message(chat_id=unban_id, text=get_translation(unban_lang, 'unbanned'))
            await update.message.reply_text(get_translation(lang, 'unbanned_user'))
        else:
            await update.message.reply_text(get_translation(lang, 'not_banned'))
    except Exception:
        await update.message.reply_text(get_translation(lang, 'error_id'))

async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_user_language(user_id)
    first_name = update.effective_user.first_name
    username = update.effective_user.username
    update_user_info(user_id, first_name, username)
    if not is_admin(user_id):
        await update.message.reply_text(get_translation(lang, 'admin_only'))
        return
    args = context.args
    if not args:
        await update.message.reply_text(get_translation(lang, 'warn_usage'))
        return
    try:
        warn_id = int(args[0])
        warn_lang = get_user_language(warn_id)
        await context.bot.send_message(chat_id=warn_id, text=get_translation(warn_lang, 'warn_message'), parse_mode="HTML")
        await update.message.reply_text(get_translation(lang, 'warned_user', user_id=warn_id))
    except Exception:
        await update.message.reply_text(get_translation(lang, 'error_id'))

async def post_init(application: Application):
    await set_bot_commands(application)

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lang", lang))
    app.add_handler(CommandHandler("mystats", mystats))
    app.add_handler(CommandHandler("blacklist", blacklist))
    app.add_handler(CommandHandler("url", url_command))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    print("Bot ishga tushdi...")
    app.run_polling()

if __name__ == "__main__":
    main()