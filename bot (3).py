"""
Javonim bot: parol, QR kod va matn -> ovoz.
Ikki usulda boshqariladi:
  1) oddiy chat: pastdagi tugmalar bilan
  2) Mini App: "📲 Mini App'ni ochish" tugmasi orqali

Pydroid 3 da o'rnatish (menyu -> Pip), bittadan:
    python-telegram-bot
    edge-tts
    segno

Ishga tushirish: TOKENni pastga qo'ying va ▶ tugmasini bosing.
"""
import html
import json
import logging
import os
import secrets
import time
from io import BytesIO

import edge_tts
import segno
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================== SOZLAMALAR ==============================
# BotFather bergan tokenni qo'shtirnoq ichiga qo'ying. Tokenni hech kimga bermang!
TOKEN = os.environ.get("BOT_TOKEN") or "BU_YERGA_TOKENNI_QOYING"

# Mini App sahifasining manzili (https:// bilan boshlanadi).
# Hozircha bo'sh qoldirsangiz ham bo'ladi: bot chatda to'liq ishlaydi,
# Mini App tugmasi esa manzil qo'yilgandan keyin paydo bo'ladi.
WEBAPP_URL = os.environ.get("WEBAPP_URL") or ""

VOICES = {
    "madina": ("Madina (ayol)", "uz-UZ-MadinaNeural"),
    "sardor": ("Sardor (erkak)", "uz-UZ-SardorNeural"),
}
MAX_VOICE_LEN = 1000   # ovoz uchun eng ko'p belgi
MAX_QR_LEN = 900       # QR uchun eng ko'p belgi
COOLDOWN = 2.0         # soniya: ketma-ket juda tez so'rovlarni cheklaydi

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("javonim")


def webapp_ready() -> bool:
    return WEBAPP_URL.startswith("https://")


# ============================== PAROL ==============================
LOWER = "abcdefghijkmnpqrstuvwxyz"
UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
DIGITS = "23456789"
SYMBOLS = "!@#$%&*?"
CONSONANTS = "bdfgjklmnprstvz"
VOWELS = "aeiou"


def random_password(length: int = 14) -> str:
    """Tasodifiy kuchli parol: kichik, katta harf, raqam va belgi albatta bor."""
    pools = (LOWER, UPPER, DIGITS, SYMBOLS)
    everything = "".join(pools)
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice(everything) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def readable_password() -> str:
    """O'qilishi oson parol: 3 ta bo'lak, har biri 3 bo'g'indan (masalan Kadolu-Mizepa-Tonaxi-47!)."""

    def chunk() -> str:
        return "".join(
            secrets.choice(CONSONANTS) + secrets.choice(VOWELS) for _ in range(3)
        ).capitalize()

    return "-".join(chunk() for _ in range(3)) + f"-{secrets.randbelow(90) + 10}!"


# ============================== TUGMALAR ==============================
def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows]
    )


def main_menu():
    return kb(
        [("🔐 Parol yasash", "m:pass")],
        [("📱 QR kod", "m:qr")],
        [("🔊 Matn → ovoz", "m:voice")],
    )


def pass_menu():
    return kb(
        [("10 ta belgi", "p:r:10"), ("14 ta", "p:r:14"), ("18 ta", "p:r:18")],
        [("🗣 O'qilishi oson", "p:w")],
        [("⬅️ Menyu", "m:menu")],
    )


def pass_result_menu(again: str):
    return kb([("🔄 Yangi parol", again)], [("⚙️ Boshqa tur", "m:pass"), ("⬅️ Menyu", "m:menu")])


def voice_menu(current: str):
    row = [
        (("✅ " if key == current else "") + name, f"v:{key}")
        for key, (name, _) in VOICES.items()
    ]
    return kb(row, [("⬅️ Menyu", "m:menu")])


def back_menu():
    return kb([("⬅️ Menyu", "m:menu")])


def mini_app_keyboard() -> ReplyKeyboardMarkup:
    """Pastda doim turadigan tugma. Mini App'dan botga ma'lumot yuborish faqat shu tugma orqali ishlaydi."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📲 Mini App'ni ochish", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
        is_persistent=True,
    )


def menu_text() -> str:
    text = "Salom! 👋 Men Javonim botman.\n"
    if webapp_ready():
        text += "Meni ikki usulda ishlatsangiz bo'ladi: shu chatda yoki 📲 Mini App orqali.\n"
    return text + "Nima qilamiz? Pastdan tanlang:"


# ============================== YORDAMCHI ==============================
async def safe_edit(query, text, markup, as_html=False):
    """Xabarni o'zgartiradi. Matn o'zgarmagan bo'lsa chiqadigan xatoni e'tiborsiz qoldiradi."""
    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML if as_html else None,
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


def too_fast(ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    now = time.monotonic()
    if now - ctx.user_data.get("last", 0.0) < COOLDOWN:
        return True
    ctx.user_data["last"] = now
    return False


# ============================== BUYRUQLAR ==============================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["mode"] = None
    if webapp_ready():
        await update.message.reply_text(
            "📲 Mini App tugmasi pastda turadi. Parol, QR, ovoz va javon shu yerda qulay.",
            reply_markup=mini_app_keyboard(),
        )
    await update.message.reply_text(menu_text(), reply_markup=main_menu())


async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["mode"] = None
    await update.message.reply_text(menu_text(), reply_markup=main_menu())


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔐 Parol: kuchli parol yasaydi (bot uni saqlamaydi).\n"
        "📱 QR kod: link yoki matndan QR rasm yasaydi.\n"
        "🔊 Ovoz: matnni o'zbekcha ovozli xabar qiladi.\n\n"
        "Menyuga qaytish: /menu",
        reply_markup=main_menu(),
    )


# ============================== TUGMALAR ISHLASHI ==============================
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await q.answer()

    if data == "m:menu":
        ctx.user_data["mode"] = None
        await safe_edit(q, menu_text(), main_menu())

    elif data == "m:pass":
        ctx.user_data["mode"] = None
        await safe_edit(q, "🔐 Qanday parol kerak?", pass_menu())

    elif data.startswith("p:"):
        if data == "p:w":
            pw = readable_password()
        else:
            n = int(data.split(":")[2])
            if n not in (10, 14, 18):
                return
            pw = random_password(n)
        text = (
            "🔐 Parolingiz:\n\n"
            f"<code>{html.escape(pw)}</code>\n\n"
            "Bosib turib nusxa oling. Bot parolni saqlamaydi, "
            "nusxa olgach bu xabarni o'chirib qo'ying."
        )
        await safe_edit(q, text, pass_result_menu(data), as_html=True)

    elif data == "m:qr":
        ctx.user_data["mode"] = "qr"
        await safe_edit(
            q,
            "📱 QR kod uchun link yoki matn yuboring.\nMasalan: https://t.me/username",
            back_menu(),
        )

    elif data == "m:voice":
        ctx.user_data["mode"] = "voice"
        current = ctx.user_data.get("voice", "madina")
        await safe_edit(
            q,
            "🔊 Ovozni tanlang, keyin matn yuboring (lotin yozuvida, "
            f"{MAX_VOICE_LEN} belgigacha).",
            voice_menu(current),
        )

    elif data.startswith("v:"):
        key = data.split(":")[1]
        if key in VOICES:
            ctx.user_data["voice"] = key
            ctx.user_data["mode"] = "voice"
            await safe_edit(
                q,
                f"🔊 {VOICES[key][0]} tanlandi. Endi matn yuboring.",
                voice_menu(key),
            )


# ============================== ASOSIY ISHLAR (chat ham, Mini App ham shuni ishlatadi) ==============================
async def do_qr(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str):
    msg = update.effective_message
    if len(text) > MAX_QR_LEN:
        await msg.reply_text(f"Matn juda uzun. {MAX_QR_LEN} belgigacha yuboring (hozir {len(text)} ta).")
        return
    if too_fast(ctx):
        await msg.reply_text("Biroz kuting va qayta yuboring ⏳")
        return
    try:
        await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
        buf = BytesIO()
        segno.make(text, error="m").save(buf, kind="png", scale=10, border=3)
        buf.seek(0)
        await msg.reply_photo(
            InputFile(buf, filename="qr.png"),
            caption="✅ QR kod tayyor.",
            reply_markup=back_menu(),
        )
    except Exception:
        log.exception("QR xatosi")
        await msg.reply_text("QR kod yasab bo'lmadi. Boshqa matn bilan urinib ko'ring.")


async def do_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, key: str = None):
    msg = update.effective_message
    if len(text) > MAX_VOICE_LEN:
        await msg.reply_text(f"Matn juda uzun. {MAX_VOICE_LEN} belgigacha yuboring (hozir {len(text)} ta).")
        return
    if too_fast(ctx):
        await msg.reply_text("Biroz kuting va qayta yuboring ⏳")
        return
    voice = VOICES[key or ctx.user_data.get("voice", "madina")][1]
    try:
        await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.RECORD_VOICE)
        audio = bytearray()
        async for part in edge_tts.Communicate(text, voice).stream():
            if part["type"] == "audio":
                audio.extend(part["data"])
        if not audio:
            raise RuntimeError("ovoz bo'sh qaytdi")
        await msg.reply_voice(InputFile(BytesIO(bytes(audio)), filename="ovoz.mp3"))
    except Exception:
        log.exception("Ovoz xatosi")
        await msg.reply_text(
            "Ovoz yasab bo'lmadi. Internetni tekshirib, birozdan keyin qayta urinib ko'ring."
        )


# ============================== XABARLAR ==============================
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Oddiy chatdagi matn: hozirgi rejimga qarab ishlaydi."""
    text = (update.message.text or "").strip()
    if not text:
        return
    mode = ctx.user_data.get("mode")
    if mode == "qr":
        await do_qr(update, ctx, text)
    elif mode == "voice":
        await do_voice(update, ctx, text)
    else:
        await update.message.reply_text("Pastdagi menyudan tanlang 👇", reply_markup=main_menu())


async def on_webapp_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Mini App'dan kelgan so'rov. Misol: {"t": "voice", "text": "Salom", "voice": "madina"}"""
    raw = update.effective_message.web_app_data.data
    try:
        payload = json.loads(raw)
        kind = payload.get("t")
        text = str(payload.get("text", "")).strip()
    except (ValueError, AttributeError):
        await update.effective_message.reply_text("Mini App'dan noto'g'ri ma'lumot keldi.")
        return
    if not text:
        await update.effective_message.reply_text("Matn bo'sh. Mini App'ga matn yozib, qayta yuboring.")
        return

    if kind == "voice":
        key = payload.get("voice")
        await do_voice(update, ctx, text, key if key in VOICES else None)
    elif kind == "qr":
        await do_qr(update, ctx, text)
    else:
        await update.effective_message.reply_text("Bu so'rov turi tanilmadi.")


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Kutilmagan xato", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Kutilmagan xato bo'ldi. /menu bosib qayta urinib ko'ring."
            )
        except Exception:
            pass


# ============================== ISHGA TUSHIRISH ==============================
def main():
    if TOKEN.startswith("BU_YERGA"):
        print("Avval TOKENni qo'ying: faylning tepasidagi TOKEN qatoriga.")
        return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    log.info("Bot ishga tushdi. Mini App: %s", "ulangan" if webapp_ready() else "hali ulanmagan")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
