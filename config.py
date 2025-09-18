# config.py

# Telegram Bot Configuration
TELEGRAM_BOT_TOKENS = [
    "8476912692:AAFyb2M2TxzkQDDHm7UbLKYuUHQkn3JQiYg",
    "8367425557:AAGmq4I73kHvJH_WUKnL6xV4SEeanfWeWps",
    "8476912692:AAFyb2M2TxzkQDDHm7UbLKYuUHQkn3JQiYg",
    "7761290445:AAEmBAXnKv-U1VK5LoUrDiNMFZ0Rxo55hdE",
    "8253369023:AAHuIhLYM6Mxspf2Flnb3KXdJCHo8Xf_1_8"
]

TELEGRAM_CHAT_ID = "7098805007"
# Stripe and API Configuration
DEFAULT_API_URL = "https://www.nutritionaledge.co.uk/my-account/add-payment-method/"
STRIPE_URL = "https://api.stripe.com/v1/payment_methods"

# Processing Configuration
MAX_WORKERS = 5
RETRY_COUNT = 3
RETRY_DELAY = 1