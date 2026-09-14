from __future__ import annotations

from fastapi import FastAPI

from .config import AppConfig, load_config


config: AppConfig = load_config()
app = FastAPI(title=config.app_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": config.service_name,
    }


@app.get("/ready")
def ready() -> dict[str, str]:
    return {
        "status": "ready",
        "service": config.service_name,
        "environment": config.environment,
    }
