"""One-shot owner notifications: validation, plain rendering, and bounded delivery."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import random
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from .config import parse_user_ids
from .telegram_api import NotificationDeliveryError, OwnerNotificationSender

logger = logging.getLogger(__name__)
KINDS = ("ready", "blocked", "failed", "update")
SOURCE_FIELDS = ("project", "client", "host", "session_id")
BIDI_CONTROLS = set("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def string_schema(limit: int) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": limit}


INPUT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["kind", "title", "message"],
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "title": string_schema(120), "message": string_schema(2000),
        "reference": {**string_schema(128), "pattern": "^[A-Za-z0-9._:-]+$"},
        "silent": {"type": "boolean", "default": False},
        "source": {"type": "object", "additionalProperties": False,
                   "properties": {key: string_schema(120) for key in SOURCE_FIELDS}},
        "link": {"type": "object", "additionalProperties": False, "required": ["url"],
                 "properties": {"url": string_schema(2048), "label": string_schema(40)}},
    },
}

ERROR_MESSAGES = {
    "validation": "Invalid notification fields or rendered text exceeds 3500 UTF-16 units; correct the payload.",
    "configuration": "Notification settings or credentials are unavailable or invalid.",
    "destination_rejected": "The configured destination is not the verified private owner.",
    "telegram_rejected": "Telegram rejected the send; inspect sender or destination configuration.",
    "rate_limited": "Telegram rate limited this call; honor the returned delay before retrying.",
    "unavailable": "Owner preflight did not complete; no notification was sent.",
    "delivery_unknown": "A send may have been accepted; another call may duplicate it.",
}


class InvalidNotification(Exception):
    def __init__(self, field: str):
        self.field = field


def _object(value, allowed, required=(), *, field="payload"):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise InvalidNotification(field)
    return value


def _text(value, limit: int, *, field: str, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise InvalidNotification(field)
    value = value.replace("\r\n", "\n")
    if not value.strip() or len(value) > limit:
        raise InvalidNotification(field)
    for char in value:
        if char in BIDI_CONTROLS or unicodedata.category(char) == "Cs":
            raise InvalidNotification(field)
        if unicodedata.category(char) == "Cc" and not (multiline and char in "\n\t"):
            raise InvalidNotification(field)
        if not multiline and char in "\u2028\u2029":
            raise InvalidNotification(field)
    return value


def _link(value) -> dict:
    value = _object(value, ("url", "label"), ("url",), field="link")
    url = _text(value["url"], 2048, field="link.url")
    if any(c.isspace() for c in url) or "\\" in url or re.search(r"%(?![0-9a-fA-F]{2})", url):
        raise ValueError("invalid URL")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("invalid URL")
    if parsed.port is not None and not 0 < parsed.port <= 65535:
        raise ValueError("invalid port")
    if not re.fullmatch(r"(?:\[[^\]]+\]|[^:\[\]]+)(?::[0-9]+)?", parsed.netloc):
        raise ValueError("invalid authority")
    host = parsed.hostname
    if ":" in host:
        ipaddress.IPv6Address(host)
    else:
        ascii_host = host.encode("idna").decode("ascii")
        if len(ascii_host) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in ascii_host.rstrip(".").split(".")
        ):
            raise ValueError("invalid host")
    return {"url": url, "label": _text(value.get("label", "Open work"), 40, field="link.label")}


def normalize_and_render(arguments, call_id: str) -> tuple[dict, str]:
    args = _object(arguments, INPUT_SCHEMA["properties"], ("kind", "title", "message"))
    if not isinstance(args["kind"], str) or args["kind"] not in KINDS:
        raise InvalidNotification("kind")
    title = _text(args["title"], 120, field="title")
    message = _text(args["message"], 2000, field="message", multiline=True)
    reference = args.get("reference", call_id)
    if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", reference):
        raise InvalidNotification("reference")
    silent = args.get("silent", False)
    if type(silent) is not bool:
        raise InvalidNotification("silent")
    source = {key: _text(value, 120, field=f"source.{key}") for key, value in
              _object(args.get("source", {}), SOURCE_FIELDS, field="source").items()}
    try:
        link = _link(args["link"]) if "link" in args else None
    except (ValueError, TypeError, UnicodeError):
        raise InvalidNotification("link.url") from None
    heading = args["kind"].upper() + (f" | {source['project']}" if "project" in source else "")
    identity = [source[key] for key in ("client", "host") if key in source]
    if "session_id" in source:
        identity.append(f"session {source['session_id']}")
    text = f"{heading}\n{title}\n\n{message}\n\n"
    if identity:
        text += " | ".join(identity) + "\n"
    text += f"Ref: {reference}"
    if len(text.encode("utf-16-le")) // 2 > 3500:
        raise InvalidNotification("rendered_text (maximum 3500 UTF-16 units)")
    return {"reference": reference, "silent": silent, "link": link}, text


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    deadline: float = 60
    backoff: float = 1
    network_timeout: float = 30

    def __post_init__(self):
        if type(self.attempts) is not int or self.attempts < 1:
            raise ValueError("invalid attempts")
        if any(not math.isfinite(v) or v <= 0 for v in (self.deadline, self.backoff, self.network_timeout)):
            raise ValueError("invalid retry budget")


@dataclass(frozen=True)
class NotificationSettings:
    token: str = field(repr=False)
    chat_id: int
    allowed_user_ids: frozenset[int]
    policy: RetryPolicy = field(default_factory=RetryPolicy)

    @classmethod
    def from_file(cls, path: Path):
        # No ambient environment or interpolation: only this operator-selected
        # file supplies settings, even when invoked through SSH AcceptEnv.
        with path.open() as stream:
            values = dotenv_values(stream=stream, interpolate=False)
        token = values.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("missing token")
        return cls(token, int(values["TELEGRAM_DEFAULT_CHAT_ID"] or ""),
                   parse_user_ids(values.get("TELEGRAM_ALLOWED_USER_IDS") or ""), RetryPolicy(
                       attempts=int(values.get("AGENT_NOTIFY_ATTEMPTS") or "3"),
                       deadline=float(values.get("AGENT_NOTIFY_DEADLINE_SECONDS") or "60"),
                       backoff=float(values.get("AGENT_NOTIFY_BACKOFF_SECONDS") or "1"),
                       network_timeout=float(values.get("AGENT_NOTIFY_NETWORK_TIMEOUT_SECONDS") or "30"),
                   ))


class NotificationService:
    def __init__(self, sender: OwnerNotificationSender | None, *, policy: RetryPolicy | None = None,
                 clock=time.monotonic, sleep=asyncio.sleep, jitter=random.random):
        self.sender = sender
        self.policy = policy or RetryPolicy()
        self.clock, self.sleep, self.jitter = clock, sleep, jitter

    async def notify(self, arguments) -> dict:
        call_id = secrets.token_hex(8)
        started = self.clock()
        attempts = 0
        uncertain = False
        possible_duplicate = False
        retry_at = None
        options = None

        def event(state, code=None):
            logger.info(json.dumps({"call_id": call_id, "timestamp": time.time(), "state": state,
                                    "code": code, "send_attempts": attempts,
                                    "duration_seconds": round(self.clock() - started, 3)}))

        def failure(code, *, invalid_field=None):
            if uncertain:
                code = "delivery_unknown"
            action = ("fix_input" if code == "validation" else "fix_configuration" if code in
                      ("configuration", "destination_rejected", "telegram_rejected") else "retry_later")
            result = {"state": "unknown" if uncertain else "not_sent", "code": code,
                      "message": ERROR_MESSAGES[code], "call_id": call_id, "next_action": action,
                      "send_attempts": attempts, "possible_duplicate": possible_duplicate}
            if invalid_field is not None:
                result["message"] = f"Invalid {invalid_field}; correct the notification payload."
            if options is not None:
                result["reference"] = options["reference"]
            if retry_at is not None:
                result["retry_after_seconds"] = max(0, math.ceil(retry_at - self.clock()))
            event(result["state"], code)
            return result

        try:
            options, text = normalize_and_render(arguments, call_id)
        except InvalidNotification as error:
            return failure("validation", invalid_field=error.field)
        if self.sender is None:
            return failure("configuration")

        deadline = started + self.policy.deadline
        last_code = "unavailable"
        sending = False

        def remaining():
            return max(0, deadline - self.clock())

        try:
            # This outer timeout bounds the entire call, not each socket phase.
            async with asyncio.timeout(remaining()):
                await self.sender.validate(min(self.policy.network_timeout, remaining()))
                while attempts < self.policy.attempts and remaining() > 0:
                    possible_duplicate |= uncertain
                    attempts += 1
                    sending = True
                    try:
                        message_id = await self.sender.send(
                            text, silent=options["silent"], link=options["link"],
                            timeout=min(self.policy.network_timeout, remaining()),
                        )
                    except NotificationDeliveryError as error:
                        uncertain |= error.uncertain
                        last_code = error.code
                        retry_at = self.clock() + error.retry_after if error.retry_after is not None else None
                        sending = False
                        if error.code not in ("delivery_unknown", "rate_limited"):
                            break
                        if attempts >= self.policy.attempts:
                            break
                        backoff = self.policy.backoff * 2.0 ** min(attempts - 1, 30)
                        delay = max(backoff * (1 + self.jitter()), error.retry_after or 0)
                        if delay >= remaining():
                            break
                        event("retry", error.code)
                        await self.sleep(delay)
                    else:
                        event("sent")
                        return {"state": "sent", "call_id": call_id, "reference": options["reference"],
                                "telegram_message_id": message_id, "send_attempts": attempts,
                                "possible_duplicate": possible_duplicate}
        except NotificationDeliveryError as error:
            last_code = error.code
            retry_at = self.clock() + error.retry_after if error.retry_after is not None else None
        except Exception:
            # Includes timeout and unexpected adapter failures; never echo them.
            uncertain |= sending
        return failure(last_code)
