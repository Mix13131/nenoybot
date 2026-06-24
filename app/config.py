from pathlib import Path
import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - lets the CLI run before dependencies are installed.
    def load_dotenv(*_args, **_kwargs):
        return False


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH)


class AppConfig:
    project_root = PROJECT_ROOT
    prompts_dir = PROMPTS_DIR
    default_goal = os.getenv("NENOYBOT_DEFAULT_GOAL", "").strip() or None
    strictness = os.getenv("NENOYBOT_STRICTNESS", "firm").strip().lower()
    timezone = os.getenv("NENOYBOT_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    reminder_check_interval = int(os.getenv("NENOYBOT_REMINDER_CHECK_INTERVAL", "30").strip() or "30")
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
    telegram_poll_timeout = int(os.getenv("TELEGRAM_POLL_TIMEOUT", "30").strip() or "30")
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip() or None
    openai_model = os.getenv("OPENAI_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    database_url = os.getenv("DATABASE_URL", "").strip() or None
