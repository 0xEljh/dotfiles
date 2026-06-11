from __future__ import annotations

import httpx

TELEGRAM_MESSAGE_LIMIT = 4096


def send_message(token: str, chat_id: int, text: str) -> int:
    """Send a plain-text message; returns the Telegram message id."""
    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:TELEGRAM_MESSAGE_LIMIT],
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {data.get('description', resp.status_code)}")
    return data["result"]["message_id"]
