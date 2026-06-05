# 🤖 Hair Bot — Інструкція з запуску

## Що робить бот

**Воронка:**
1. Людина пише кодове слово ГАЙД у коментарях/директі Instagram
2. ManyChat автоматично надсилає посилання на цього TG-бота
3. Бот вітає, надсилає безкоштовний гайд (PDF)
4. Після гайду — 3 продажні повідомлення + кнопка «Купити курс — $39»
5. Оплата: WayForPay (картою) або крипта (BTC/USDT)
6. Після оплати — автоматичний доступ до курсу

---

## Крок 1 — Створи бота в Telegram

1. Відкрий [@BotFather](https://t.me/BotFather)
2. Надішли `/newbot`
3. Придумай назву і username (наприклад `HairProtocolBot`)
4. Скопіюй **токен** — він потрібен для `BOT_TOKEN`

---

## Крок 2 — Налаштуй файл .env

Скопіюй `.env.example` → `.env` і заповни:

```
BOT_TOKEN=         ← токен від BotFather
ADMIN_ID=          ← твій Telegram ID (отримай у @userinfobot)
GUIDE_PDF_PATH=guide.pdf
COURSE_LINK=       ← посилання на закритий канал/папку з курсом

WAYFORPAY_MERCHANT_ACCOUNT=   ← назва мерчанта у WayForPay
WAYFORPAY_MERCHANT_KEY=       ← секретний ключ з кабінету WayForPay
WAYFORPAY_DOMAIN=             ← домен, вказаний у WayForPay

CRYPTO_WALLET_BTC=            ← Bitcoin адреса
CRYPTO_WALLET_USDT=           ← USDT TRC-20 адреса
```

---

## Крок 3 — Завантаж файли на Railway

### Варіант A: через GitHub (рекомендовано)

1. Створи репозиторій на GitHub
2. Завантаж усі файли цієї папки + `guide.pdf` (твій гайд)
3. Зайди на [railway.app](https://railway.app) → New Project → Deploy from GitHub
4. Вибери репозиторій
5. У розділі **Variables** додай усі змінні з `.env`
6. Деплой запуститься автоматично ✅

### Варіант B: Railway CLI

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

---

## Крок 4 — WayForPay webhook

У кабінеті WayForPay → Налаштування → Webhook URL встанови:

```
https://YOUR_RAILWAY_URL/webhook/wayforpay
```

Railway URL виглядає як: `https://hairbot-production.up.railway.app`

---

## Крок 5 — ManyChat (Instagram → Telegram)

1. У ManyChat створи автоматизацію:
   - **Тригер:** коментар або DM містить слово `ГАЙД`
   - **Дія:** надіслати повідомлення з кнопкою
   - **Текст:** `Лови гайд в Telegram 👇`
   - **Кнопка:** посилання `https://t.me/YOUR_BOT_USERNAME?start=ig`

2. Опціонально: можна додати `?start=ig` щоб відстежувати трафік з Instagram

---

## Адмін-команди (тільки для тебе)

| Команда | Що робить |
|---------|-----------|
| `/stats` | Кількість користувачів і конверсія |
| `/approve USER_ID` | Вручну видати доступ (після крипто-оплати) |
| `/support` | Показати контакт підтримки |

---

## Структура файлів

```
hairbot/
├── bot.py          ← головний файл бота
├── config.py       ← налаштування
├── messages.py     ← усі тексти повідомлень (редагуй тут)
├── db.py           ← база даних (SQLite)
├── requirements.txt
├── Procfile
├── railway.toml
├── .env.example    ← шаблон змінних середовища
└── guide.pdf       ← ← ← ПОКЛАДИ СЮДИ СВІЙ ГАЙД
```

---

## Що змінити у messages.py

- `MSG_SUPPORT` — замінити `@your_support_username` на свій username
- Тексти можна редагувати як завгодно, не чіпаючи логіку бота

---

## Питання?

Якщо щось не запускається — перевір:
1. `BOT_TOKEN` правильний?
2. `guide.pdf` лежить поруч з `bot.py`?
3. У Railway додані всі Variables?
