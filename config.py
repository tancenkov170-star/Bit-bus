import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@jopmenjer")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/jopmenjer")

DB_PATH = os.getenv("DB_PATH", "jopa.db")

# Настройки по умолчанию
WARN_LIMIT = 3
WARN_MUTE_HOURS = 1

FLOOD_LIMIT = 5
FLOOD_WINDOW = 5
FLOOD_MUTE_MINUTES = 5

CAPS_MIN_LEN = 10      # сообщение длиннее N символов
CAPS_MIN_PERCENT = 70  # и на N% из заглавных → нарушение

AUTODELETE_SECONDS = 15
SUB_RECHECK_INTERVAL = 3600
