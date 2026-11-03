import asyncio
import json
import os
import sys
from datetime import timedelta

import jsonschema
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.message import SessionMessage

from personal_telegram_bot.notification_mcp import OUTPUT_SCHEMA, create_server
from personal_telegram_bot.notifications import NotificationService


def test_wire_discovery_structured_error_fallback_and_sanitized_sdk_failures(tmp_path):
    async def go():
        errors = tmp_path / "stderr"
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "personal_telegram_bot.notification_mcp", "--env-file", str(tmp_path / "missing.env")],
            env={**os.environ, "TELEGRAM_BOT_TOKEN": "DO_NOT_USE_AMBIENT_TOKEN"},
        )
        with errors.open("w+") as errlog:
            async with stdio_client(params, errlog=errlog) as (reader, writer):
                async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=5)) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    assert [t.name for t in tools] == ["notify_user"]
                    tool = tools[0]
                    assert tool.outputSchema == OUTPUT_SCHEMA
                    assert tool.annotations.idempotentHint is False
                    assert tool.annotations.readOnlyHint is False
                    assert tool.annotations.openWorldHint is True
                    assert tool.annotations.destructiveHint is False
                    for args, code in [
                        ({"kind": "ready", "title": "Ready", "message": "PRIVATE_MESSAGE"}, "configuration"),
                        ({"kind": "ready", "title": "Ready", "message": "PRIVATE_MESSAGE", "token": "PRIVATE_TOKEN"}, "validation"),
                    ]:
                        result = await session.call_tool("notify_user", args)
                        assert result.isError is True
                        assert result.structuredContent["code"] == code
                        assert json.loads(result.content[0].text) == result.structuredContent
                        jsonschema.validate(result.structuredContent, tool.outputSchema)
                        assert "PRIVATE" not in result.model_dump_json()
                    # Bad protocol argument types fail before our tool handler.
                    # The SDK must not leak the rejected input through its logs.
                    await writer.send(SessionMessage(
                        types.JSONRPCMessage.model_validate({
                            "jsonrpc": "2.0", "id": 99, "method": "tools/call",
                            "params": {"name": "notify_user", "arguments": "PRIVATE_SDK_VALUE"},
                        })
                    ))
                    await asyncio.sleep(0.02)
            errlog.seek(0)
            logs = errlog.read()
        assert "PRIVATE" not in logs and "DO_NOT_USE_AMBIENT_TOKEN" not in logs
    asyncio.run(go())


def test_server_result_has_matching_success_text_and_schema():
    class Sender:
        async def validate(self, timeout):
            pass

        async def send(self, *args, **kwargs):
            return 42

    async def go():
        from mcp import types

        server = create_server(NotificationService(Sender()))
        result = await server.request_handlers[types.CallToolRequest](types.CallToolRequest(
            method="tools/call", params=types.CallToolRequestParams(
                name="notify_user", arguments={"kind": "ready", "title": "Ready", "message": "Done"},
            ),
        ))
        body = result.root
        assert body.isError is False
        assert body.structuredContent["telegram_message_id"] == 42
        assert json.loads(body.content[0].text) == body.structuredContent
        jsonschema.validate(body.structuredContent, OUTPUT_SCHEMA)
    asyncio.run(go())


def test_eof_cancels_pending_work_without_retry_or_detached_delivery():
    import anyio
    from mcp import types
    from mcp.shared.message import SessionMessage

    from personal_telegram_bot.notification_mcp import run_connected

    async def go():
        started = anyio.Event()
        cancelled = anyio.Event()
        attempts = []

        class Sender:
            async def validate(self, timeout):
                pass

            async def send(self, *args, **kwargs):
                attempts.append(1)
                started.set()
                try:
                    await anyio.sleep_forever()
                finally:
                    cancelled.set()

        incoming_send, incoming_read = anyio.create_memory_object_stream(10)
        outgoing_send, outgoing_read = anyio.create_memory_object_stream(10)
        server = create_server(NotificationService(Sender()))
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run_connected, server, incoming_read, outgoing_send)
            for message in [
                {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            ]:
                await incoming_send.send(SessionMessage(types.JSONRPCMessage.model_validate({"jsonrpc": "2.0", **message})))
            await outgoing_read.receive()
            await incoming_send.send(SessionMessage(types.JSONRPCMessage.model_validate({"jsonrpc": "2.0", "method": "notifications/initialized"})))
            await incoming_send.send(SessionMessage(types.JSONRPCMessage.model_validate({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "notify_user", "arguments": {"kind": "ready", "title": "Ready", "message": "Done"}},
            })))
            with anyio.fail_after(2):
                await started.wait()
                await incoming_send.aclose()
                await cancelled.wait()
        assert attempts == [1]
    asyncio.run(go())


def test_sigterm_exits_cleanly_even_while_client_stdin_remains_open(tmp_path):
    async def go():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "personal_telegram_bot.notification_mcp",
            "--env-file", str(tmp_path / "missing.env"), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            process.stdin.write((json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "1"}},
            }) + "\n").encode())
            await process.stdin.drain()
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
            assert response["id"] == 1
            process.terminate()
            assert await asyncio.wait_for(process.wait(), 2) == 0
            assert await process.stderr.read() == b""
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    asyncio.run(go())
