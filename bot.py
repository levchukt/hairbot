import logging
import os
import hashlib
import hmac
import time
import asyncio
import re

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, MessageHandler, filters,
    ContextTypes
)
from aiohttp import web
import aiohttp

from config import (
    BOT_TOKEN, GUIDE_PDF_PATH, WAYFORPAY_MERCHANT_ACCOUNT,
    WAYFORPAY_MERCHANT_KEY, WAYFORPAY_DOMAIN, COURSE_PRICE_USD,
    COURSE_NAME, CRYPTO_WALLET_BTC, CRYPTO_WALLET_USDT, ADMIN_ID,
    CRYPTOBOT_API_TOKEN, CRYPTOBOT_API_URL, OFFER_FOLLOWUP_DELAY_SECONDS,
    STARS_PRICE, STARS_SHOP_URL, RU_SOURCE_PREFIX
)
from messages import (
    MSG_WELCOME, MSG_GUIDE_CAPTION, MSG_PAIN, MSG_OFFER, MSG_FOLLOWUP,
    MSG_FOLLOWUP_DAY1, MSG_FOLLOWUP_DAY3,
    MSG_PAYMENT_CHOOSE, MSG_PAYMENT_CRYPTO_CHOOSE, MSG_PAYMENT_CRYPTO_MANUAL,
    MSG_PAYMENT_STARS, MSG_PAYMENT_STARS_SHOP, MSG_PAYMENT_CRYPTO_AUTO,
    MSG_PAYMENT_SUCCESS, MSG_PAYMENT_ALREADY,
    MSG_SUPPORT
)
from db import Database

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

db = Database()

# Дозволені символи для джерела трафіку (?start=...).
# Будь-який новий тег (reel01, dht, cortisol...) працює без правок коду —
# і водночас фільтрує сміття/спецсимволи перед записом у БД.
SOURCE_RE = re.compile(r'^[a-z0-9_]{1,20}$')


# ─────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    source = "direct"
    if context.args:
        raw = context.args[0].lower()
        if SOURCE_RE.match(raw):
            source = raw

    is_new = not db.user_exists(user.id)
    db.add_user(user.id, user.username, user.first_name, source=source)
    db.log_event(user.id, "start")

    if not is_new:
        # Повторний /start — просто нагадуємо
        if db.is_paid(user.id):
            await update.message.reply_text("У тебя уже есть доступ к протоколу 👇", parse_mode="HTML")
            await send_course_access(user.id, context)
        else:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"💳 Открыть полный протокол — ${COURSE_PRICE_USD}", callback_data="buy_course")
            ]])
            await update.message.reply_text(
                "Гайд ты уже получил 👆\n\nГотов перейти к полному протоколу?",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        return

    await update.message.reply_text(MSG_WELCOME, parse_mode="HTML")
    await asyncio.sleep(2)
    await send_guide(update, context)


# ─────────────────────────────────────────────
#  GUIDE
# ─────────────────────────────────────────────

def _save_file_id_to_env(file_id: str):
    env_path = ".env"
    if not os.path.exists(env_path):
        return
    with open(env_path, "r") as f:
        content = f.read()
    if "GUIDE_FILE_ID=" in content:
        lines = content.splitlines()
        lines = [f"GUIDE_FILE_ID={file_id}" if l.startswith("GUIDE_FILE_ID=") else l for l in lines]
        content = "\n".join(lines)
    else:
        content += f"\nGUIDE_FILE_ID={file_id}\n"
    with open(env_path, "w") as f:
        f.write(content)
    logger.info("GUIDE_FILE_ID saved to .env")


async def send_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cached_file_id = os.getenv("GUIDE_FILE_ID", "")

    if cached_file_id:
        await context.bot.send_document(
            chat_id=user_id,
            document=cached_file_id,
            caption=MSG_GUIDE_CAPTION,
            parse_mode="HTML",
        )
    else:
        logger.info("First upload of guide PDF...")
        with open(GUIDE_PDF_PATH, "rb") as f:
            msg = await context.bot.send_document(
                chat_id=user_id,
                document=f,
                caption=MSG_GUIDE_CAPTION,
                parse_mode="HTML",
                write_timeout=120,
                read_timeout=120,
                connect_timeout=30,
            )
        file_id = msg.document.file_id
        logger.info(f"Guide uploaded. FILE_ID: {file_id}")
        _save_file_id_to_env(file_id)
        if ADMIN_ID:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"✅ <b>Гайд завантажено!</b>\n\nДодай в Railway Variables:\n<code>GUIDE_FILE_ID={file_id}</code>",
                parse_mode="HTML"
            )

    await asyncio.sleep(1)

    # Кнопка під гайдом
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Я прочитал", callback_data="guide_read")
    ]])
    await context.bot.send_message(
        chat_id=user_id,
        text="Как только прочитаешь — нажми кнопку, пришлю кое-что важное 👇",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

    # Страховка — через 24 год якщо кнопку не натиснули
    db.log_event(user_id, "guide_sent")

    context.job_queue.run_once(
        scheduled_sales_fallback,
        when=86400,
        chat_id=user_id,
        user_id=user_id,
        name=f"sales_fallback_{user_id}"
    )


# ─────────────────────────────────────────────
#  SALES SEQUENCE
# ─────────────────────────────────────────────

async def guide_read_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    await query.edit_message_reply_markup(reply_markup=None)
    db.log_event(user_id, "guide_read")

    # Якщо вже отримав оффер або купив — не надсилаємо повторно
    if db.has_offer_sent(user_id) or db.is_paid(user_id):
        return

    # Скасовуємо страховку
    for job in context.job_queue.get_jobs_by_name(f"sales_fallback_{user_id}"):
        job.schedule_removal()

    await send_sales_sequence(user_id, context)


async def scheduled_sales_fallback(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    # Не надсилаємо якщо вже купив або вже отримав оффер через кнопку
    if db.is_paid(user_id) or db.has_offer_sent(user_id):
        return
    await send_sales_sequence(user_id, context)


async def send_sales_sequence(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    db.mark_offer_sent(user_id)
    db.log_event(user_id, "offer_sent")

    await context.bot.send_message(chat_id=user_id, text=MSG_PAIN, parse_mode="HTML")
    await asyncio.sleep(10)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"💳 Купить протокол — ${COURSE_PRICE_USD}", callback_data="buy_course")
    ]])
    await context.bot.send_message(
        chat_id=user_id,
        text=MSG_OFFER,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

    # Не продажне нагадування через 30-60 хв, якщо досі не оплатив
    context.job_queue.run_once(
        scheduled_offer_followup,
        when=OFFER_FOLLOWUP_DELAY_SECONDS,
        chat_id=user_id,
        user_id=user_id,
        name=f"offer_followup_{user_id}"
    )

    # Догрівочна серія — день 1 і день 3, якщо досі не оплатив
    context.job_queue.run_once(
        scheduled_followup_day1,
        when=86400,
        chat_id=user_id,
        user_id=user_id,
        name=f"followup_day1_{user_id}"
    )
    context.job_queue.run_once(
        scheduled_followup_day3,
        when=86400 * 3,
        chat_id=user_id,
        user_id=user_id,
        name=f"followup_day3_{user_id}"
    )


async def scheduled_offer_followup(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    if db.is_paid(user_id):
        return
    db.log_event(user_id, "followup_1h")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Открыть полный протокол", callback_data="buy_course")
    ]])
    await context.bot.send_message(
        chat_id=user_id,
        text=MSG_FOLLOWUP,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def scheduled_followup_day1(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    if db.is_paid(user_id):
        return
    db.log_event(user_id, "followup_day1")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"💳 Купить протокол — ${COURSE_PRICE_USD}", callback_data="buy_course")
    ]])
    await context.bot.send_message(
        chat_id=user_id,
        text=MSG_FOLLOWUP_DAY1,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def scheduled_followup_day3(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    if db.is_paid(user_id):
        return
    db.log_event(user_id, "followup_day3")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"💳 Купить протокол — ${COURSE_PRICE_USD}", callback_data="buy_course")
    ]])
    await context.bot.send_message(
        chat_id=user_id,
        text=MSG_FOLLOWUP_DAY3,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
#  BUY FLOW
# ─────────────────────────────────────────────

async def buy_course(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db.log_event(user_id, "buy_click")

    if db.is_paid(user_id):
        await query.message.reply_text(MSG_PAYMENT_ALREADY, parse_mode="HTML")
        await send_course_access(user_id, context)
        return

    keyboard = InlineKeyboardMarkup(_payment_keyboard(user_id))
    await query.message.reply_text(
        MSG_PAYMENT_CHOOSE,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


def _is_ru_traffic(user_id: int) -> bool:
    """Джерела на кшталт ru_reel01 → російський трафік.
    Картка для них не показується: російські Visa/MC не проходять
    міжнародний процесинг, кнопка лише зжирає конверсію."""
    return db.get_source(user_id).startswith(RU_SOURCE_PREFIX)


def _payment_keyboard(user_id: int) -> list:
    stars_btn = [InlineKeyboardButton(
        f"⭐ Оплатить звёздами — {STARS_PRICE}", callback_data="pay_stars"
    )]
    crypto_btn = [InlineKeyboardButton("₿ Оплатить криптой", callback_data="crypto_menu")]
    card_btn = [InlineKeyboardButton("💳 Оплатить картой", callback_data="pay_wayforpay")]
    help_btn = [InlineKeyboardButton("❓ Вопрос / помощь", callback_data="support")]

    if _is_ru_traffic(user_id):
        return [stars_btn, crypto_btn, help_btn]
    return [card_btn, stars_btn, crypto_btn, help_btn]


async def pay_wayforpay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db.log_event(user_id, "pay_card_click")

    # Посилання на наш сервер який робить POST-редирект на WayForPay
    base_url = os.getenv("RAILWAY_PUBLIC_URL", f"http://localhost:{os.getenv('PORT', 8080)}")
    payment_url = f"{base_url}/pay/{user_id}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data="check_payment")],
    ])
    await query.message.reply_text(
        f"<b>Оплата через WayForPay</b>\n\n"
        f"Сумма: <b>${COURSE_PRICE_USD}</b>\n\n"
        f"Перейди к оплате и после успешной оплаты вернись сюда и нажми «Я оплатил».\n\n"
        f"⚡️ Доступ откроется автоматически после подтверждения.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
#  TELEGRAM STARS
# ─────────────────────────────────────────────

async def pay_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db.log_event(user_id, "pay_stars_click")

    if db.is_paid(user_id):
        await query.message.reply_text(MSG_PAYMENT_ALREADY, parse_mode="HTML")
        await send_course_access(user_id, context)
        return

    text = MSG_PAYMENT_STARS.format(stars=STARS_PRICE)
    buttons = []
    if STARS_SHOP_URL:
        text += MSG_PAYMENT_STARS_SHOP
        buttons.append([InlineKeyboardButton("Купить звёзды за рубли", url=STARS_SHOP_URL)])
    buttons.append([InlineKeyboardButton("❓ Вопрос / помощь", callback_data="support")])

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML"
    )

    # Для XTR provider_token не потрібен, а сума в prices — це кількість зірок
    # напряму (без множення на 100, як у фіатних валютах).
    await context.bot.send_invoice(
        chat_id=user_id,
        title=COURSE_NAME,
        description="Полный протокол: 6 фаз, анализы, добавки, чек-листы, таймлайн.",
        payload=f"protocol_{user_id}",
        provider_token=None,
        currency="XTR",
        prices=[LabeledPrice(label=COURSE_NAME, amount=STARS_PRICE)],
    )
    db.log_event(user_id, "stars_invoice_sent")


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram чекає відповідь до 10 секунд. Не відповіси — платіж зірветься."""
    query = update.pre_checkout_query
    user_id = query.from_user.id

    if not query.invoice_payload.startswith("protocol_"):
        await query.answer(ok=False, error_message="Счёт устарел. Открой оплату заново.")
        return

    if db.is_paid(user_id):
        await query.answer(ok=False, error_message="У тебя уже есть доступ — оплата не нужна.")
        return

    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user_id = update.effective_user.id

    # Зберігаємо charge_id — без нього refundStarPayment неможливий
    db.save_stars_charge(user_id, payment.telegram_payment_charge_id)
    db.mark_paid(user_id, method="stars")
    logger.info(
        f"Stars payment: user={user_id} amount={payment.total_amount} "
        f"charge_id={payment.telegram_payment_charge_id}"
    )

    await update.message.reply_text(MSG_PAYMENT_SUCCESS, parse_mode="HTML")
    await send_course_access(user_id, context)

    if ADMIN_ID:
        username = update.effective_user.username or str(user_id)
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⭐ <b>Оплата зірками</b> — @{username} (ID: <code>{user_id}</code>)\n"
                 f"Сума: {payment.total_amount} XTR\n"
                 f"Повернення: <code>/refund {user_id}</code>",
            parse_mode="HTML"
        )


async def crypto_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db.log_event(query.from_user.id, "pay_crypto_click")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Быстро через CryptoBot", callback_data="pay_crypto_auto")],
        [InlineKeyboardButton("📋 Перевести вручную", callback_data="pay_crypto_manual")],
        [InlineKeyboardButton("❓ Вопрос / помощь", callback_data="support")],
    ])
    await query.message.reply_text(
        MSG_PAYMENT_CRYPTO_CHOOSE,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def create_cryptobot_invoice(user_id: int) -> str | None:
    """Створює інвойс у CryptoBot, повертає посилання на оплату."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{CRYPTOBOT_API_URL}/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN},
            json={
                # Фіатний інвойс: курс фіксує CryptoBot на своєму боці,
                # юзер обирає актив уже всередині оплати.
                # Якщо API поверне помилку по TON — перевір актуальний код
                # активу після ребрендингу Toncoin → GRAM.
                "currency_type": "fiat",
                "fiat": "USD",
                "accepted_assets": "USDT,TON",
                "amount": str(COURSE_PRICE_USD),
                "description": COURSE_NAME,
                "payload": str(user_id),
                "paid_btn_name": "openBot",
                "paid_btn_url": "https://t.me/protocol_hair_bot",
            }
        ) as resp:
            result = await resp.json()
            if result.get("ok"):
                return result["result"]["bot_invoice_url"]
            logger.error(f"CryptoBot createInvoice failed: {result}")
            return None


async def pay_crypto_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db.log_event(user_id, "crypto_auto_invoice")

    invoice_url = await create_cryptobot_invoice(user_id)
    if not invoice_url:
        await query.message.reply_text(
            "⚠️ Не удалось создать счёт. Попробуй «Перевести вручную» или напиши /support",
            parse_mode="HTML"
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ Оплатить через CryptoBot", url=invoice_url)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data="check_payment")],
    ])
    await query.message.reply_text(
        MSG_PAYMENT_CRYPTO_AUTO.format(amount=COURSE_PRICE_USD),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def pay_crypto_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db.log_event(query.from_user.id, "crypto_manual_open")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Я отправил", callback_data="crypto_manual_sent")],
        [InlineKeyboardButton("❓ Нужна помощь", callback_data="support")],
    ])
    await query.message.reply_text(
        MSG_PAYMENT_CRYPTO_MANUAL.format(
            amount=COURSE_PRICE_USD,
            btc=CRYPTO_WALLET_BTC,
            usdt=CRYPTO_WALLET_USDT,
        ),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def crypto_manual_sent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db.log_event(query.from_user.id, "crypto_manual_declared")
    user_id = query.from_user.id
    username = query.from_user.username or str(user_id)

    await query.message.reply_text(
        "✅ Принято! Проверим транзакцию и откроем доступ в течение <b>1–2 часов</b>.\n\n"
        "Если вопросы — напиши /support",
        parse_mode="HTML"
    )
    if ADMIN_ID:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 <b>Ручная крипто-оплата от @{username}</b> (ID: {user_id})\n"
                 f"Используй /approve {user_id} для выдачи доступа.",
            parse_mode="HTML"
        )


async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db.log_event(user_id, "check_payment_click")

    if db.is_paid(user_id):
        await query.message.reply_text(MSG_PAYMENT_SUCCESS, parse_mode="HTML")
        await send_course_access(user_id, context)
    else:
        await query.message.reply_text(
            "⏳ Оплата пока не подтверждена.\n\n"
            "Попробуй через минуту — или напиши /support если оплатил, но доступ не открылся.",
            parse_mode="HTML"
        )


async def send_course_access(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    course_link = os.getenv("COURSE_LINK", "https://t.me/your_course_channel")
    await context.bot.send_message(
        chat_id=user_id,
        text=f"🎉 <b>Добро пожаловать в протокол!</b>\n\n"
             f"Доступ к <b>{COURSE_NAME}</b> открыт.\n\n"
             f"👉 <a href='{course_link}'>Перейти к материалам</a>\n\n"
             f"Если ссылка не работает — напиши /support",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
#  ADMIN
# ─────────────────────────────────────────────

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    try:
        target_id = int(context.args[0])
        db.mark_paid(target_id, method="crypto_manual")
        await update.message.reply_text(f"✅ Пользователь {target_id} получил доступ.")
        await send_course_access(target_id, context)
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /approve USER_ID")


async def admin_refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/refund USER_ID — повертає зірки і знімає доступ.
    Працює лише для оплат через Stars: у карти й крипти повернення руками."""
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /refund USER_ID")
        return

    charge_id = db.get_stars_charge(target_id)
    if not charge_id:
        await update.message.reply_text(
            f"У {target_id} немає оплати зірками. "
            f"Повернення по карті/крипті роби вручну."
        )
        return

    try:
        await context.bot.refund_star_payment(
            user_id=target_id, telegram_payment_charge_id=charge_id
        )
    except Exception as e:
        logger.error(f"Refund failed for {target_id}: {e}")
        await update.message.reply_text(f"❌ Помилка повернення: {e}")
        return

    db.unmark_paid(target_id)
    await update.message.reply_text(f"✅ Зірки повернуто, доступ знято: {target_id}")
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text="Звёзды возвращены на твой баланс. Доступ к протоколу закрыт.\n\n"
                 "Если это ошибка — напиши /support",
        )
    except Exception:
        pass


FUNNEL_STEPS = [
    ("start",      "Запустили бота"),
    ("guide_sent", "Получили гайд"),
    ("guide_read", "Нажали «Прочитал»"),
    ("offer_sent", "Увидели оффер"),
    ("buy_click",  "Нажали «Купить»"),
    ("paid",       "Оплатили"),
]

SOURCE_LABELS = {"ig1": "Instagram 1", "ig2": "Instagram 2", "ig3": "Instagram 3", "direct": "Прямой вход"}


def _bar(pct: float, width: int = 10) -> str:
    filled = int(round(pct / 100 * width))
    return "\u2588" * filled + "\u2591" * (width - filled)


def _build_funnel_text(counts: dict) -> str:
    base = counts.get("start", 0)
    lines = []
    prev = None
    for key, label in FUNNEL_STEPS:
        n = counts.get(key, 0)
        pct = (n / base * 100) if base else 0
        drop = ""
        if prev is not None and prev > 0 and n < prev:
            drop = f"  <i>\u2212{prev - n}</i>"
        lines.append(f"{_bar(pct)} <b>{n}</b> \u00b7 {pct:.0f}%  {label}{drop}")
        prev = n
    return "\n".join(lines)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return

    stats = db.get_stats()
    counts = db.event_counts()
    stuck = db.stuck_users()

    source_lines = ""
    for s in stats["sources"]:
        label = SOURCE_LABELS.get(s["source"], s["source"])
        conv = (s["paid"] / s["total"] * 100) if s["total"] else 0
        source_lines += f"\n  {label}: {s['total']} \u2192 {s['paid']} оплат ({conv:.1f}%)"

    text = (
        f"📊 <b>ВОРОНКА</b>\n\n"
        f"{_build_funnel_text(counts)}\n\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"🔴 <b>ГДЕ ЗАСТРЕВАЮТ</b>\n\n"
        f"Получили гайд, не открыли: <b>{stuck['guide_not_read']}</b>\n"
        f"Увидели оффер, не кликнули: <b>{stuck['offer_no_click']}</b>\n"
        f"Кликнули, не выбрали оплату: <b>{stuck['buy_no_method']}</b>\n"
        f"Выбрали оплату, не заплатили: <b>{stuck['method_no_pay']}</b>\n"
        f"Кликнули «Купить», не купили: <b>{stuck['buy_no_pay']}</b>\n\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"💳 <b>СПОСОБЫ ОПЛАТЫ</b>\n\n"
        f"Выбрали карту: <b>{counts.get('pay_card_click', 0)}</b>\n"
        f"  \u2192 дошли до страницы: <b>{counts.get('pay_page_open', 0)}</b>\n"
        f"Выбрали звёзды: <b>{counts.get('pay_stars_click', 0)}</b>\n"
        f"  \u2192 получили счёт: <b>{counts.get('stars_invoice_sent', 0)}</b>\n"
        f"Выбрали крипту: <b>{counts.get('pay_crypto_click', 0)}</b>\n"
        f"  \u2192 CryptoBot: <b>{counts.get('crypto_auto_invoice', 0)}</b>\n"
        f"  \u2192 вручную: <b>{counts.get('crypto_manual_open', 0)}</b>\n"
        f"Нажали «Я оплатил»: <b>{counts.get('check_payment_click', 0)}</b>\n\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"📬 <b>ДОГРЕВЫ</b>\n\n"
        f"Через час: <b>{counts.get('followup_1h', 0)}</b>\n"
        f"День 1: <b>{counts.get('followup_day1', 0)}</b>\n"
        f"День 3: <b>{counts.get('followup_day3', 0)}</b>\n\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"🌐 <b>ИСТОЧНИКИ</b>{source_lines}\n\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"👥 Всего: <b>{stats['total_users']}</b>\n"
        f"💰 Оплатили: <b>{stats['paid_users']}</b>\n"
        f"📈 Конверсия: <b>{stats['conversion']:.1f}%</b>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def admin_funnel_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/funnel ig1 — воронка по конкретному джерелу."""
    if str(update.effective_user.id) != str(ADMIN_ID):
        return
    if not context.args:
        await update.message.reply_text("Использование: /funnel ig1")
        return
    source = context.args[0].lower()
    counts = db.event_counts(source=source)
    label = SOURCE_LABELS.get(source, source)
    if not counts:
        await update.message.reply_text(f"По источнику «{label}» данных пока нет.")
        return
    await update.message.reply_text(
        f"📊 <b>ВОРОНКА — {label}</b>\n\n{_build_funnel_text(counts)}",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
#  SUPPORT
# ─────────────────────────────────────────────

async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(MSG_SUPPORT, parse_mode="HTML")


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MSG_SUPPORT, parse_mode="HTML")


# ─────────────────────────────────────────────
#  WAYFORPAY WEBHOOK
# ─────────────────────────────────────────────

def generate_wayforpay_data(user_id: int) -> dict:
    """Повертає всі поля для POST-форми WayForPay."""
    order_id = f"hair_{user_id}_{int(time.time())}"
    order_date = int(time.time())

    sign_string = (
        f"{WAYFORPAY_MERCHANT_ACCOUNT};{WAYFORPAY_DOMAIN};{order_id};"
        f"{order_date};{COURSE_PRICE_USD};USD;{COURSE_NAME};1;{COURSE_PRICE_USD}"
    )
    signature = hmac.new(
        WAYFORPAY_MERCHANT_KEY.encode(),
        sign_string.encode(),
        hashlib.md5
    ).hexdigest()

    return {
        "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
        "merchantDomainName": WAYFORPAY_DOMAIN,
        "orderReference": order_id,
        "orderDate": str(order_date),
        "amount": str(COURSE_PRICE_USD),
        "currency": "USD",
        "productName": COURSE_NAME,
        "productCount": "1",
        "productPrice": str(COURSE_PRICE_USD),
        "merchantSignature": signature,
         "returnUrl": "https://t.me/protocol_hair_bot",
        "serviceUrl": "https://web-production-cb698.up.railway.app/webhook/wayforpay",
    }


def generate_wayforpay_html(user_id: int) -> str:
    """Генерує HTML-сторінку яка автоматично відправляє POST на WayForPay."""
    data = generate_wayforpay_data(user_id)

    fields_html = ""
    for key, value in data.items():
        fields_html += f'        <input type="hidden" name="{key}" value="{value}">\n'

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Перехід до оплати...</title>
  <style>
    body {{ font-family: sans-serif; display: flex; justify-content: center;
           align-items: center; height: 100vh; margin: 0; background: #f5f5f5; }}
    .box {{ text-align: center; }}
    p {{ color: #555; }}
  </style>
</head>
<body>
  <div class="box">
    <p>Переходим к оплате...</p>
    <form id="wfp" method="POST" action="https://secure.wayforpay.com/pay">
{fields_html}    </form>
  </div>
  <script>document.getElementById("wfp").submit();</script>
</body>
</html>"""


async def wayforpay_redirect(request: web.Request) -> web.Response:
    """GET /pay/{user_id} — повертає HTML-форму яка POST-ить на WayForPay."""
    try:
        user_id = int(request.match_info["user_id"])
    except (KeyError, ValueError):
        return web.Response(status=400, text="Bad user_id")
    html = generate_wayforpay_html(user_id)
    db.log_event(user_id, "pay_page_open")
    return web.Response(text=html, content_type="text/html")


async def wayforpay_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400)

    expected_fields = [
        "merchantAccount", "orderReference", "amount",
        "currency", "authCode", "cardPan", "transactionStatus", "reasonCode"
    ]
    sign_string = ";".join(str(data.get(f, "")) for f in expected_fields)
    expected_sig = hmac.new(
        WAYFORPAY_MERCHANT_KEY.encode(),
        sign_string.encode(),
        hashlib.md5
    ).hexdigest()

    if data.get("merchantSignature") != expected_sig:
        logger.warning("WayForPay: invalid signature")
        return web.Response(status=403)

    if data.get("transactionStatus") == "Approved":
        order_ref = data.get("orderReference", "")
        try:
            user_id = int(order_ref.split("_")[1])
            db.mark_paid(user_id, method="wayforpay")
            logger.info(f"Payment confirmed for user {user_id}")
            bot_app = request.app.get("bot_app")
            if bot_app:
                await bot_app.bot.send_message(
                    chat_id=user_id,
                    text=MSG_PAYMENT_SUCCESS,
                    parse_mode="HTML"
                )
                course_link = os.getenv("COURSE_LINK", "https://t.me/your_course_channel")
                access_text = (
                    f"🎉 <b>Добро пожаловать в протокол!</b>\n\n"
                    f"Доступ к <b>{COURSE_NAME}</b> открыт.\n\n"
                    f'👉 <a href="{course_link}">Перейти к материалам</a>\n\n'
                    f"Если ссылка не работает — напиши /support"
                )
                await bot_app.bot.send_message(
                    chat_id=user_id,
                    text=access_text,
                    parse_mode="HTML"
                )
        except (IndexError, ValueError) as e:
            logger.error(f"Can't parse user_id from {order_ref}: {e}")

    accept_time = int(time.time())
    sign_resp = hmac.new(
        WAYFORPAY_MERCHANT_KEY.encode(),
        f"{data.get('orderReference')};accept;{accept_time}".encode(),
        hashlib.md5
    ).hexdigest()

    return web.json_response({
        "orderReference": data.get("orderReference"),
        "status": "accept",
        "time": accept_time,
        "signature": sign_resp
    })


# ─────────────────────────────────────────────
#  CRYPTOBOT WEBHOOK
# ─────────────────────────────────────────────

async def cryptobot_webhook(request: web.Request) -> web.Response:
    raw_body = await request.read()
    signature = request.headers.get("crypto-pay-api-signature", "")

    secret = hashlib.sha256(CRYPTOBOT_API_TOKEN.encode()).digest()
    expected_sig = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()

    if signature != expected_sig:
        logger.warning("CryptoBot: invalid signature")
        return web.Response(status=403)

    data = await request.json()

    if data.get("update_type") == "invoice_paid":
        invoice = data.get("payload", {})
        try:
            user_id = int(invoice.get("payload", ""))
            db.mark_paid(user_id, method="cryptobot")
            logger.info(f"Crypto payment confirmed for user {user_id}")
            bot_app = request.app.get("bot_app")
            if bot_app:
                course_link = os.getenv("COURSE_LINK", "https://t.me/your_course_channel")
                access_text = (
                    f"🎉 <b>Добро пожаловать в протокол!</b>\n\n"
                    f"Доступ к <b>{COURSE_NAME}</b> открыт.\n\n"
                    f'👉 <a href="{course_link}">Перейти к материалам</a>\n\n'
                    f"Если ссылка не работает — напиши /support"
                )
                await bot_app.bot.send_message(chat_id=user_id, text=access_text, parse_mode="HTML")
        except (TypeError, ValueError) as e:
            logger.error(f"Can't parse user_id from CryptoBot payload: {e}")

    return web.Response(status=200, text="OK")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CommandHandler("approve", admin_approve))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("funnel", admin_funnel_source))
    app.add_handler(CommandHandler("refund", admin_refund))
    app.add_handler(CallbackQueryHandler(guide_read_callback, pattern="^guide_read$"))
    app.add_handler(CallbackQueryHandler(buy_course, pattern="^buy_course$"))
    app.add_handler(CallbackQueryHandler(pay_wayforpay, pattern="^pay_wayforpay$"))
    app.add_handler(CallbackQueryHandler(pay_stars, pattern="^pay_stars$"))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(CallbackQueryHandler(crypto_menu, pattern="^crypto_menu$"))
    app.add_handler(CallbackQueryHandler(pay_crypto_auto, pattern="^pay_crypto_auto$"))
    app.add_handler(CallbackQueryHandler(pay_crypto_manual, pattern="^pay_crypto_manual$"))
    app.add_handler(CallbackQueryHandler(crypto_manual_sent, pattern="^crypto_manual_sent$"))
    app.add_handler(CallbackQueryHandler(check_payment, pattern="^check_payment$"))
    app.add_handler(CallbackQueryHandler(support_callback, pattern="^support$"))

    web_app = web.Application()
    web_app["bot_app"] = app
    web_app.router.add_post("/webhook/wayforpay", wayforpay_webhook)
    web_app.router.add_post("/webhook/cryptobot", cryptobot_webhook)
    web_app.router.add_get("/pay/{user_id}", wayforpay_redirect)

    webhook_port = int(os.getenv("PORT", 8080))

    await app.initialize()
    await app.start()

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", webhook_port)
    await site.start()

    logger.info(f"Bot started. Webhook server on port {webhook_port}")

    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
