import logging
import os
import hashlib
import hmac
import json
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)
from aiohttp import web
import aiohttp

from config import (
    BOT_TOKEN, GUIDE_PDF_PATH, WAYFORPAY_MERCHANT_ACCOUNT,
    WAYFORPAY_MERCHANT_KEY, WAYFORPAY_DOMAIN, COURSE_PRICE_USD,
    COURSE_NAME, CRYPTO_WALLET_BTC, CRYPTO_WALLET_USDT, ADMIN_ID
)
from messages import (
    MSG_WELCOME, MSG_GUIDE_CAPTION, MSG_PAIN, MSG_OFFER,
    MSG_PAYMENT_CHOOSE, MSG_PAYMENT_CRYPTO,
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


# ─────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────

async def guide_read_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Користувач натиснув 'Я прочитал'."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Знімаємо кнопку
    await query.edit_message_reply_markup(reply_markup=None)

    # Скасовуємо страховочний таймер
    jobs = context.job_queue.get_jobs_by_name(f"sales_fallback_{user_id}")
    for job in jobs:
        job.schedule_removal()

    # Одразу надсилаємо оффер
    await send_sales_sequence(user_id, context)


async def scheduled_sales_fallback(context: ContextTypes.DEFAULT_TYPE):
    """Страховка — спрацьовує через 24 год якщо кнопку не натиснули."""
    user_id = context.job.user_id

    # Не надсилаємо якщо вже купив
    if db.is_paid(user_id):
        return

    await send_sales_sequence(user_id, context)


async def scheduled_sales(context: ContextTypes.DEFAULT_TYPE):
    """Викликається job_queue через 12 хвилин після отримання гайду."""
    user_id = context.job.user_id
    await send_sales_sequence(user_id, context)


async def send_sales_sequence(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Біль → пауза 8 сек → оффер з кнопкою."""
    await context.bot.send_message(chat_id=user_id, text=MSG_PAIN, parse_mode="HTML")
    await asyncio.sleep(8)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Купить протокол — $39", callback_data="buy_course")
    ]])

    await context.bot.send_message(
        chat_id=user_id,
        text=MSG_OFFER,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Визначаємо джерело з параметра ?start=ig1
    source = "direct"
    if context.args:
        raw = context.args[0].lower()
        if raw in ("ig1", "ig2", "ig3"):
            source = raw

    db.add_user(user.id, user.username, user.first_name, source=source)

    await update.message.reply_text(
        MSG_WELCOME,
        parse_mode="HTML"
    )

    await asyncio.sleep(2)
    await send_guide(update, context)


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
        # Миттєво — файл вже є на серверах Telegram
        await context.bot.send_document(
            chat_id=user_id,
            document=cached_file_id,
            caption=MSG_GUIDE_CAPTION,
            parse_mode="HTML",
        )
    else:
        # Перший раз — завантажуємо і зберігаємо file_id
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
                text=f"\u2705 <b>Гайд завантажено!</b>\n\nДодай в Railway Variables:\n<code>GUIDE_FILE_ID={file_id}</code>",
                parse_mode="HTML"
            )

    # Надсилаємо продажне повідомлення через 12 хвилин
    # Страховка — через 24 год якщо кнопку не натиснули
    context.job_queue.run_once(
        scheduled_sales_fallback,
        when=30,
        chat_id=user_id,
        user_id=user_id,
        name=f"sales_fallback_{user_id}"
    )


async def send_sales_sequence(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """2 повідомлення: біль → оффер з кнопкою."""
    await context.bot.send_message(chat_id=user_id, text=MSG_PAIN, parse_mode="HTML")
    await asyncio.sleep(5)

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Купить протокол — $39", callback_data="buy_course")
    ]])

    await context.bot.send_message(
        chat_id=user_id,
        text=MSG_OFFER,
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

    if db.is_paid(user_id):
        await query.message.reply_text(MSG_PAYMENT_ALREADY, parse_mode="HTML")
        await send_course_access(user_id, context)
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇦 Оплатить картой (WayForPay)", callback_data="pay_wayforpay")],
        [InlineKeyboardButton("₿ Оплатить криптой", callback_data="pay_crypto")],
        [InlineKeyboardButton("❓ Вопрос / помощь", callback_data="support")],
    ])

    await query.message.reply_text(
        "Выбери удобный способ оплаты:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def pay_wayforpay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    payment_url = generate_wayforpay_url(user_id)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Перейти к оплате", url=payment_url)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data="check_payment")],
    ])

    await query.message.reply_text(
        f"<b>Оплата через WayForPay</b>\n\n"
        f"Сумма: <b>${COURSE_PRICE_USD}</b>\n\n"
        f"Нажми кнопку ниже, перейди к оплате, и после успешной оплаты "
        f"вернись сюда и нажми «Я оплатил».\n\n"
        f"⚡️ Доступ к курсу откроется автоматически после подтверждения оплаты.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Я отправил", callback_data="crypto_sent")],
        [InlineKeyboardButton("❓ Нужна помощь", callback_data="support")],
    ])

    await query.message.reply_text(
        MSG_PAYMENT_CRYPTO.format(
            btc=CRYPTO_WALLET_BTC,
            usdt=CRYPTO_WALLET_USDT,
            amount=COURSE_PRICE_USD
        ),
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def crypto_sent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or str(user_id)

    await query.message.reply_text(
        "✅ Принято! Мы проверим транзакцию и откроем доступ в течение <b>1–2 часов</b>.\n\n"
        "Если возникнут вопросы — напиши /support",
        parse_mode="HTML"
    )

    # Notify admin
    if ADMIN_ID:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 <b>Крипто-оплата от @{username}</b> (ID: {user_id})\n"
                 f"Нужна ручная проверка. Используй /approve {user_id} для выдачи доступа.",
            parse_mode="HTML"
        )


async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

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
    """Send course access link/content to paid user."""
    # TODO: замените COURSE_LINK на реальную ссылку
    course_link = os.getenv("COURSE_LINK", "https://t.me/your_course_channel")

    await context.bot.send_message(
        chat_id=user_id,
        text=f"🎉 <b>Добро пожаловать в курс!</b>\n\n"
             f"Твой доступ к <b>{COURSE_NAME}</b> открыт.\n\n"
             f"👉 <a href='{course_link}'>Перейти к материалам курса</a>\n\n"
             f"Если ссылка не работает — напиши /support",
        parse_mode="HTML"
    )


# ─────────────────────────────────────────────
#  ADMIN COMMANDS
# ─────────────────────────────────────────────

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: manually approve a user. Usage: /approve USER_ID"""
    if str(update.effective_user.id) != str(ADMIN_ID):
        return

    try:
        target_id = int(context.args[0])
        db.mark_paid(target_id, method="crypto_manual")
        await update.message.reply_text(f"✅ Пользователь {target_id} получил доступ.")
        await send_course_access(target_id, context)
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /approve USER_ID")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: show stats."""
    if str(update.effective_user.id) != str(ADMIN_ID):
        return

    stats = db.get_stats()

    # Build per-source breakdown
    source_lines = ""
    source_labels = {"ig1": "Instagram 1", "ig2": "Instagram 2", "ig3": "Instagram 3", "direct": "Прямой вход"}
    for s in stats["sources"]:
        label = source_labels.get(s["source"], s["source"])
        conv = (s["paid"] / s["total"] * 100) if s["total"] else 0
        source_lines += f"\n  {label}: {s['total']} чел. / {s['paid']} оплат ({conv:.1f}%)"

    await update.message.reply_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего: <b>{stats['total_users']}</b>\n"
        f"💰 Оплатили: <b>{stats['paid_users']}</b>\n"
        f"📈 Общая конверсия: <b>{stats['conversion']:.1f}%</b>\n\n"
        f"<b>По источникам:</b>{source_lines}",
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

def generate_wayforpay_url(user_id: int) -> str:
    """Generate WayForPay payment URL."""
    import urllib.parse

    order_id = f"hair_{user_id}_{int(time.time())}"
    order_date = int(time.time())

    params = {
        "merchantAccount": WAYFORPAY_MERCHANT_ACCOUNT,
        "merchantDomainName": WAYFORPAY_DOMAIN,
        "orderReference": order_id,
        "orderDate": order_date,
        "amount": COURSE_PRICE_USD,
        "currency": "USD",
        "productName[]": COURSE_NAME,
        "productCount[]": "1",
        "productPrice[]": COURSE_PRICE_USD,
    }

    # Generate signature
    sign_string = (
        f"{WAYFORPAY_MERCHANT_ACCOUNT};"
        f"{WAYFORPAY_DOMAIN};"
        f"{order_id};"
        f"{order_date};"
        f"{COURSE_PRICE_USD};"
        f"USD;"
        f"{COURSE_NAME};"
        f"1;"
        f"{COURSE_PRICE_USD}"
    )
    signature = hmac.new(
        WAYFORPAY_MERCHANT_KEY.encode(),
        sign_string.encode(),
        hashlib.md5
    ).hexdigest()

    params["merchantSignature"] = signature

    base_url = "https://secure.wayforpay.com/pay"
    return f"{base_url}?{urllib.parse.urlencode(params)}"


async def wayforpay_webhook(request: web.Request) -> web.Response:
    """Handle WayForPay payment confirmation webhook."""
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400)

    # Verify signature
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
        # order_ref format: hair_{user_id}_{timestamp}
        try:
            user_id = int(order_ref.split("_")[1])
            db.mark_paid(user_id, method="wayforpay")
            logger.info(f"Payment confirmed for user {user_id}")

            # Access will be sent by check_payment button press
            # Or we can push it proactively via bot:
            app = request.app.get("bot_app")
            if app:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=MSG_PAYMENT_SUCCESS,
                    parse_mode="HTML"
                )
                await send_course_access(user_id, app.bot)

        except (IndexError, ValueError) as e:
            logger.error(f"Can't parse user_id from order_ref: {order_ref} — {e}")

    # WayForPay expects this response
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
#  MAIN
# ─────────────────────────────────────────────

import asyncio


async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CommandHandler("approve", admin_approve))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CallbackQueryHandler(guide_read_callback, pattern="^guide_read$"))
    app.add_handler(CallbackQueryHandler(buy_course, pattern="^buy_course$"))
    app.add_handler(CallbackQueryHandler(pay_wayforpay, pattern="^pay_wayforpay$"))
    app.add_handler(CallbackQueryHandler(pay_crypto, pattern="^pay_crypto$"))
    app.add_handler(CallbackQueryHandler(crypto_sent, pattern="^crypto_sent$"))
    app.add_handler(CallbackQueryHandler(check_payment, pattern="^check_payment$"))
    app.add_handler(CallbackQueryHandler(support_callback, pattern="^support$"))

    # Webhook server for WayForPay
    web_app = web.Application()
    web_app["bot_app"] = app
    web_app.router.add_post("/webhook/wayforpay", wayforpay_webhook)

    webhook_port = int(os.getenv("PORT", 8080))

    # Run bot + web server together
    await app.initialize()
    await app.start()

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", webhook_port)
    await site.start()

    logger.info(f"Bot started. Webhook server on port {webhook_port}")

    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
