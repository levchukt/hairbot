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

# Course
COURSE_NAME = "ПОЛНЫЙ ПРОТОКОЛ ВОССТАНОВЛЕНИЯ ВОЛОС"
COURSE_PRICE_USD = 1
