import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_ID", "")

GUIDE_PDF_PATH = os.getenv("GUIDE_PDF_PATH", "guide.pdf")

# WayForPay
WAYFORPAY_MERCHANT_ACCOUNT = os.getenv("WAYFORPAY_MERCHANT_ACCOUNT", "")
WAYFORPAY_MERCHANT_KEY = os.getenv("WAYFORPAY_MERCHANT_KEY", "")
WAYFORPAY_DOMAIN = os.getenv("WAYFORPAY_DOMAIN", "yourdomain.com")

# Crypto wallets
CRYPTO_WALLET_BTC = os.getenv("CRYPTO_WALLET_BTC", "")
CRYPTO_WALLET_USDT = os.getenv("CRYPTO_WALLET_USDT", "")  # TRC-20

# CryptoBot (Crypto Pay API)
CRYPTOBOT_API_TOKEN = os.getenv("CRYPTOBOT_API_TOKEN", "")
CRYPTOBOT_API_URL = os.getenv("CRYPTOBOT_API_URL", "https://pay.crypt.bot/api")

# Telegram Stars (XTR)
# Telegram виплачує ~$0.013 за зірку. Щоб отримати $9 нетто → ~700 зірок.
# Перевіряй курс перед зміною ціни: він плаваючий.
STARS_PRICE = int(os.getenv("STARS_PRICE", "700"))

# Посилання на сервіс купівлі зірок за рублі (СБП).
# ОБОВ'ЯЗКОВО протестуй сервіс сам перед тим, як вести туди трафік.
# Якщо порожнє — кнопка «Купить за рубли» не показується.
STARS_SHOP_URL = os.getenv("STARS_SHOP_URL", "")

# Джерела трафіку, що починаються з цього префікса, отримують
# порядок кнопок «Stars → крипта» (без картки).
# Приклад тегів: ru_reel01, ru_dht, ru_cortisol
RU_SOURCE_PREFIX = os.getenv("RU_SOURCE_PREFIX", "ru")

# Course
COURSE_NAME = "ПОЛНЫЙ ПРОТОКОЛ ВОССТАНОВЛЕНИЯ ВОЛОС"
COURSE_PRICE_USD = int(os.getenv("COURSE_PRICE_USD", "19"))

# Follow-up після офера, якщо людина не оплатила (секунди). За замовчуванням 45 хв.
OFFER_FOLLOWUP_DELAY_SECONDS = int(os.getenv("OFFER_FOLLOWUP_DELAY_SECONDS", "2700"))
