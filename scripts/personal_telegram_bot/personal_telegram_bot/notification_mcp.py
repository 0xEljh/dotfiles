"""Send-only MCP stdio adapter. No polling, background worker, or retry policy."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

import anyio
import httpx
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .notifications import ERROR_MESSAGES, INPUT_SCHEMA, NotificationService, NotificationSettings
from .telegram_api import OwnerNotificationSender

_COMMON_PROPERTIES = {
    "call_id": {"type": "string", "pattern": "^[a-f0-9]{16}$"},
    "reference": {"type": "string", "pattern": "^[A-Za-z0-9._:-]{1,128}$"},
    "send_attempts": {"type": "integer", "minimum": 0},
    "possible_duplicate": {
        "type": "boolean",
        "description": "True only when this call attempted another send after an uncertain attempt. "
                       "False does not make an unknown outcome safe to retry.",
    },
}
OUTPUT_SCHEMA = {
    "type": "object",
    "oneOf": [
        {"type": "object", "additionalProperties": False,
         "required": ["state", "call_id", "reference", "telegram_message_id", "send_attempts", "possible_duplicate"],
         "properties": {**_COMMON_PROPERTIES, "state": {"const": "sent"},
                        "telegram_message_id": {"type": "integer", "minimum": 1}}},
        {"type": "object", "additionalProperties": False,
         "required": ["state", "code", "message", "call_id", "next_action"],
         "properties": {**_COMMON_PROPERTIES, "state": {"enum": ["not_sent", "unknown"]},
                        "code": {"enum": list(ERROR_MESSAGES)}, "message": {"type": "string"},
                        "next_action": {"enum": ["fix_input", "fix_configuration", "retry_later"]},
                        "retry_after_seconds": {"type": "integer", "minimum": 0}}},
    ],
}

DESCRIPTION = """Send a short private Telegram check-in at a useful handoff, when human
input is required, or at an important requested milestone. Routine progress
narration is discouraged. Content leaves this host for Telegram and may appear
on a phone lock screen: omit secrets, private logs, full source files, pairing
grants and credential-bearing URLs. Source labels are caller-supplied context,
not authenticated identity; omit unknown labels and never invent session IDs or
return links. Only HTTPS work links are accepted; a link is optional. Normal
notifications are the default; silent=true is an explicit override. Sent means
Telegram acceptance, not read or acknowledged. Repeated calls and uncertain
retries can duplicate messages; reference is a correlation label, NOT an
idempotency key. Honor retry_after_seconds and avoid unbounded retry loops.
Permission to notify is not permission to perform the underlying work."""


def create_server(service: NotificationService) -> Server:
    server = Server("agent-notify", version="0.1.0")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(
            name="notify_user", description=DESCRIPTION, inputSchema=INPUT_SCHEMA, outputSchema=OUTPUT_SCHEMA,
            annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                               openWorldHint=True, idempotentHint=False),
        )]

    # SDK schema errors include supplied values. The core validates instead and
    # returns content-free structured errors, including a generated call ID.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict):
        result = await service.notify(arguments if name == "notify_user" else {})
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=True))],
            structuredContent=result, isError=result["state"] != "sent",
        )

    return server


async def run_connected(server: Server, reader, writer):
    # The SDK waits for active handlers when its input closes. Cancel our group
    # on observed EOF instead, so no retries continue after disconnection.
    forwarded_send, forwarded_read = anyio.create_memory_object_stream(0)
    async with anyio.create_task_group() as tasks:
        async def forward():
            async with forwarded_send:
                async for message in reader:
                    await forwarded_send.send(message)
            tasks.cancel_scope.cancel()

        async def run():
            try:
                await server.run(forwarded_read, writer, server.create_initialization_options())
            finally:
                tasks.cancel_scope.cancel()

        tasks.start_soon(run)
        tasks.start_soon(forward)


def configure_logging():
    # The pinned SDK logs rejected protocol payloads through the root logger;
    # HTTP debug output can also expose token-bearing URLs. Only our deliberately
    # content-free event logger is allowed to write to stderr in this process.
    logging.basicConfig(handlers=[logging.NullHandler()], force=True)
    logger = logging.getLogger("personal_telegram_bot.notifications")
    logger.handlers = [logging.StreamHandler(sys.stderr)]
    logger.setLevel(logging.INFO)
    logger.propagate = False


class _PipeInput(anyio.AsyncFile[str]):
    """Cancellable pipe reads; the SDK's default threaded readline is shielded."""

    def __init__(self, reader: asyncio.StreamReader):
        super().__init__(sys.stdin)
        self._reader = reader

    async def readline(self) -> str:
        return (await self._reader.readline()).decode("utf-8")


async def serve(env_file: Path):
    configure_logging()
    try:
        settings = NotificationSettings.from_file(env_file)
    except Exception:
        settings = None
    # A missing credential still permits tool discovery and a safe configuration
    # error. Never fall back to ambient environment or another dotenv path.
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        service = NotificationService(
            OwnerNotificationSender(settings.token, settings.chat_id, settings.allowed_user_ids, client)
            if settings else None, policy=settings.policy if settings else None,
        )
        server = create_server(service)
        pipe_reader = asyncio.StreamReader(limit=1024 * 1024)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(pipe_reader), sys.stdin.buffer,
        )
        async with stdio_server(stdin=_PipeInput(pipe_reader)) as (reader, writer):
            try:
                async with anyio.create_task_group() as tasks:
                    async def stop_on_signal():
                        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
                            async for _ in signals:
                                tasks.cancel_scope.cancel()

                    async def connected():
                        try:
                            await run_connected(server, reader, writer)
                        finally:
                            tasks.cancel_scope.cancel()

                    tasks.start_soon(stop_on_signal)
                    tasks.start_soon(connected)
            finally:
                # Wake the SDK reader before leaving its transport task group.
                transport.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Private owner notification MCP server (stdio)")
    parser.add_argument("--env-file", required=True, type=Path, help="fixed host-local runtime credential file")
    args = parser.parse_args(argv)
    try:
        asyncio.run(serve(args.env_file))
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Notification MCP transport stopped.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
