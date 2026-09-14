# Telegram credential logging hardening

Railway runtime logs must never contain the Telegram bot token. HTTP client libraries may log request URLs at INFO level, and Telegram embeds the bot token in the Bot API URL path.

The v2 worker therefore forces `httpx`, `httpx2`, `httpcore`, and `httpcore2` loggers to WARNING or higher before any runtime clients are used.

If a token has ever appeared in runtime logs, rotate it in BotFather and replace `NENOY_V2_TELEGRAM_BOT_TOKEN` on both v2 services. Old log entries are treated as compromised credential material even when project access is private.
