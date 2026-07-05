from __future__ import annotations

import httpx

TELEGRAM_MESSAGE_LIMIT = 4096


def send_message(
    token: str,
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup: dict | None = None,
) -> int:
    """Send a message; returns the Telegram message id. Plain text by default;
    pass parse_mode='HTML' (or 'MarkdownV2') to enable formatting/links — the
    caller is then responsible for escaping dynamic content."""
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:TELEGRAM_MESSAGE_LIMIT],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {data.get('description', resp.status_code)}")
    return data["result"]["message_id"]
