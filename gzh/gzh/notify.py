from __future__ import annotations

import os

import httpx


def send_telegram(message: str, chat_id: str | None = None,
                  token: str | None = None, client=httpx) -> dict:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set; skipped"}
    if not chat:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID not set; skipped"}
    resp = None
    try:
        resp = client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": message},
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__,
                "status": getattr(resp, "status_code", None)}
    return {"ok": True, "status": resp.status_code}
