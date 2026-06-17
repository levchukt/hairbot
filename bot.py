import logging
import os
import hashlib
import hmac
import time
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)
from aiohttp import web
import aiohttp

from config import (
    BOT_TOKEN, GUIDE_PDF_PATH, WAYFORPAY_MERCHANT_ACCOUNT,
    WAYFORPAY_MERCHANT_KEY, WAYFORPAY_DOMAIN, COURSE_PRICE_USD,
    COURSE_NAME, CRYPTO_WALLET_BTC, CRYPTO_WALLET_USDT, ADMIN_ID,
    CRYPTOBOT_API_TOKEN, CRYPTOBOT_API_URL
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    source = "direct"
    if context.args:
        raw = context.args[0].lower()
        if raw in ("ig1", "ig2", "ig3"):
            source = raw

    is_new = not db.user_exists(user.id)
    db.add_user(user.id, user.username, user.first_name, source=source)

    if not is_new:
        # Повторний /start — просто нагадуємо
        if db.is_paid(user.id):
            await update.message.reply_text("У тебя уже есть доступ к протоколу 👇", parse_mode="HTML")
            await send_course_access(user.id, context)
        else:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Открыть полный протокол — $39", callback_data="buy_course")
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
        [InlineKeyboardButton("Оплатить картой", callback_data="pay_wayforpay")],
        [InlineKeyboardButton("₿ Оплатить криптой", callback_data="pay_crypto")],
        [InlineKeyboardButton("❓ Вопрос / помощь", callback_data="support")],
    ])
    await query.message.reply_text(
        MSG_PAYMENT_CHOOSE,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def pay_wayforpay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

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


async def create_cryptobot_invoice(user_id: int) -> str | None:
    """Створює інвойс у CryptoBot, повертає посилання на оплату."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{CRYPTOBOT_API_URL}/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN},
            json={
                "asset": "USDT",
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


async def pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    invoice_url = await create_cryptobot_invoice(user_id)
    if not invoice_url:
        await query.message.reply_text(
            "⚠️ Не удалось создать счёт. Попробуй ещё раз или напиши /support",
            parse_mode="HTML"
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("₿ Оплатить через CryptoBot", url=invoice_url)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data="check_payment")],
    ])
    await query.message.reply_text(
        f"<b>Оплата криптовалютой</b>\n\n"
        f"Сумма: <b>${COURSE_PRICE_USD}</b> (в USDT)\n\n"
        f"Перейди к оплате и после успешной оплаты вернись сюда и нажми «Я оплатил».\n\n"
        f"⚡️ Доступ откроется автоматически после подтверждения.",
        reply_markup=keyboard,
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


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return

    stats = db.get_stats()
    source_labels = {"ig1": "Instagram 1", "ig2": "Instagram 2", "ig3": "Instagram 3", "direct": "Прямой вход"}
    source_lines = ""
    for s in stats["sources"]:
        label = source_labels.get(s["source"], s["source"])
        conv = (s["paid"] / s["total"] * 100) if s["total"] else 0
        source_lines += f"\n  {label}: {s['total']} чел. / {s['paid']} оплат ({conv:.1f}%)"

    await update.message.reply_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего: <b>{stats['total_users']}</b>\n"
        f"💰 Оплатили: <b>{stats['paid_users']}</b>\n"
        f"📈 Конверсия: <b>{stats['conversion']:.1f}%</b>\n\n"
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
    app.add_handler(CallbackQueryHandler(guide_read_callback, pattern="^guide_read$"))
    app.add_handler(CallbackQueryHandler(buy_course, pattern="^buy_course$"))
    app.add_handler(CallbackQueryHandler(pay_wayforpay, pattern="^pay_wayforpay$"))
    app.add_handler(CallbackQueryHandler(pay_crypto, pattern="^pay_crypto$"))
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
