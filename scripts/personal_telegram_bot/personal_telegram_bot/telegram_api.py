from __future__ import annotations

from collections.abc import Collection

import httpx

TELEGRAM_MESSAGE_LIMIT = 4096
PLAIN_REPORT_LIMIT = 3500


def _allowed_owner(chat_id: int, allowed_user_ids: Collection[int]) -> bool:
    return type(chat_id) is int and chat_id > 0 and chat_id in allowed_user_ids


def _matching_private_chat(chat: dict, chat_id: int) -> bool:
    return chat.get("type") == "private" and type(chat.get("id")) is int and chat["id"] == chat_id


class ReportDeliveryError(RuntimeError):
    """Content-free, machine-readable failure for one-shot private reports."""

    def __init__(self, kind: str, *, retry_after: int | None = None):
        self.kind = kind
        self.retry_after = retry_after
        super().__init__(f"private report delivery: {kind}")


def _report_request(token: str, method: str, payload: dict) -> dict:
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=30,
        )
    except httpx.HTTPError:
        raise ReportDeliveryError("transient") from None
    try:
        data = response.json()
    except ValueError:
        raise ReportDeliveryError("transient") from None
    if not isinstance(data, dict):
        raise ReportDeliveryError("transient")
    code = data.get("error_code", response.status_code)
    if code == 429 or response.status_code == 429:
        parameters = data.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if type(retry_after) is not int or retry_after <= 0:
            retry_after = None
        raise ReportDeliveryError("rate_limited", retry_after=retry_after)
    if data.get("ok") is not True or response.status_code >= 400:
        permanent = type(code) is int and 400 <= code < 500 and code != 408
        raise ReportDeliveryError("configuration" if permanent else "transient")
    result = data.get("result")
    if not isinstance(result, dict):
        raise ReportDeliveryError("transient")
    return result


def validate_private_owner(token: str, chat_id: int, allowed_user_ids: Collection[int]) -> None:
    """Require an allowlisted positive owner ID and Telegram's live private chat."""
    if not _allowed_owner(chat_id, allowed_user_ids):
        raise ReportDeliveryError("configuration")
    chat = _report_request(token, "getChat", {"chat_id": chat_id})
    if not _matching_private_chat(chat, chat_id):
        raise ReportDeliveryError("configuration")


def send_plain_report(token: str, chat_id: int, text: str, *, allowed_user_ids: Collection[int]) -> int:
    """Send supplied text unchanged; the coordinator owns splitting and retries."""
    if not text or len(text) > PLAIN_REPORT_LIMIT:
        raise ReportDeliveryError("configuration")
    validate_private_owner(token, chat_id, allowed_user_ids)
    result = _report_request(token, "sendMessage", {
        "chat_id": chat_id, "text": text, "disable_web_page_preview": True,
    })
    message_id = result.get("message_id")
    if type(message_id) is not int or message_id <= 0:
        raise ReportDeliveryError("transient")
    return message_id


class NotificationDeliveryError(RuntimeError):
    """An explicit rejection or an uncertain send, without response content."""

    def __init__(self, code: str, *, uncertain: bool = False, retry_after: int | None = None):
        self.code = code
        self.uncertain = uncertain
        self.retry_after = retry_after
        super().__init__(code)


class OwnerNotificationSender:
    """Owner-only notification boundary; the caller owns retries and deadlines."""

    def __init__(self, token: str, chat_id: int, allowed_user_ids: Collection[int], client: httpx.AsyncClient):
        self._token = token
        self._chat_id = chat_id
        self._allowed = allowed_user_ids
        self._client = client

    async def _request(self, method: str, payload: dict, timeout: float) -> dict:
        sending = method == "sendMessage"
        fallback = "delivery_unknown" if sending else "unavailable"
        try:
            response = await self._client.post(
                f"https://api.telegram.org/bot{self._token}/{method}", json=payload, timeout=timeout,
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise NotificationDeliveryError(fallback, uncertain=sending) from None
        if not isinstance(data, dict):
            raise NotificationDeliveryError(fallback, uncertain=sending)
        if response.status_code == 408 or response.status_code >= 500:
            raise NotificationDeliveryError(fallback, uncertain=sending)
        # Only a well-formed API error establishes rejection. HTTP status alone
        # cannot prove that a send was not accepted upstream.
        code = data.get("error_code")
        if data.get("ok") is False and type(code) is int and 400 <= code < 500 and code != 408:
            if code == 429:
                parameters = data.get("parameters")
                delay = parameters.get("retry_after") if isinstance(parameters, dict) else None
                if type(delay) is not int or delay <= 0:
                    delay = None
                raise NotificationDeliveryError("rate_limited", retry_after=delay)
            rejected = "configuration" if code not in (400, 403) else "destination_rejected"
            raise NotificationDeliveryError("telegram_rejected" if sending else rejected)
        result = data.get("result")
        if not (200 <= response.status_code < 300 and data.get("ok") is True and isinstance(result, dict)):
            raise NotificationDeliveryError(fallback, uncertain=sending)
        return result

    async def validate(self, timeout: float) -> None:
        if not self._token:
            raise NotificationDeliveryError("configuration")
        if not _allowed_owner(self._chat_id, self._allowed):
            raise NotificationDeliveryError("destination_rejected")
        chat = await self._request("getChat", {"chat_id": self._chat_id}, timeout)
        if type(chat.get("id")) is not int or not isinstance(chat.get("type"), str):
            raise NotificationDeliveryError("unavailable")
        if not _matching_private_chat(chat, self._chat_id):
            raise NotificationDeliveryError("destination_rejected")

    async def send(self, text: str, *, silent: bool, link: dict | None, timeout: float) -> int:
        if not text or len(text.encode("utf-16-le")) // 2 > PLAIN_REPORT_LIMIT:
            raise NotificationDeliveryError("configuration")
        payload = {
            "chat_id": self._chat_id, "text": text, "disable_notification": silent,
            "link_preview_options": {"is_disabled": True},
        }
        if link is not None:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": link["label"], "url": link["url"]}]]}
        result = await self._request("sendMessage", payload, timeout)
        message_id = result.get("message_id")
        if type(message_id) is not int or message_id <= 0:
            raise NotificationDeliveryError("delivery_unknown", uncertain=True)
        return message_id


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
