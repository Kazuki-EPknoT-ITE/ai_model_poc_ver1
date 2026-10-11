"""MCP の結合テスト: インメモリのクライアントと、stdio で起動したサーバー。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from conftest import check_envelope
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from office_fixtures import make_invoice_pdf, make_pdf, make_xlsx
from test_invoice import GOOD

import lla_protocol
from office_tools.server import PASSWORDS_META_KEY, build_server


def _env(result: types.CallToolResult) -> dict[str, Any]:
    env = result.structuredContent
    assert env is not None
    assert result.content and isinstance(result.content[0], types.TextContent)
    assert json.loads(result.content[0].text) == env
    assert result.isError == (not env["ok"])
    return env


def test_in_memory_list_and_call_with_sampling(ws: Path) -> None:
    make_xlsx(ws / "送金明細.xlsx")
    make_invoice_pdf(ws / "請求書.pdf", scanned_second_page=True)
    make_pdf(ws / "鍵.pdf", encrypt="secret")
    seen: list[types.CreateMessageRequestParams] = []

    async def sampling(context: Any, params: types.CreateMessageRequestParams) -> types.CreateMessageResult:
        seen.append(params)
        purpose = (params.metadata or {}).get("purpose")
        if purpose == "ocr":
            content = params.messages[0].content
            assert isinstance(content, types.ImageContent) and content.mimeType == "image/png"
            text = json.dumps({"text": "Remarks", "confidence": 0.9})
        else:
            assert purpose == "invoice_extract"
            assert params.metadata is not None and params.metadata["response_schema"]["title"] == "invoice.v1"
            text = json.dumps(GOOD)
        return types.CreateMessageResult(
            role="assistant", content=types.TextContent(type="text", text=text), model="fake"
        )

    async def scenario() -> None:
        server = build_server()
        async with create_connected_server_and_client_session(server, sampling_callback=sampling) as client:
            listed = await client.list_tools()
            assert len(listed.tools) == 32
            tool = next(t for t in listed.tools if t.name == "xlsx.write_cells")
            ann = tool.annotations.model_dump(by_alias=True) if tool.annotations else {}
            assert ann["x-agent-effect"] == "write" and ann["x-agent-path-args"] == ["/path"]
            assert tool.inputSchema == lla_protocol.tool("xlsx.write_cells")["args_schema"]

            env = _env(await client.call_tool("xlsx.info", {"path": "送金明細.xlsx"}))
            check_envelope("xlsx.info", env)
            assert env["data"]["sheets"][0]["name"] == "明細"

            env = _env(await client.call_tool("xlsx.info", {"path": 1}))
            assert env["error"]["code"] == "INVALID_ARGS"
            env = _env(await client.call_tool("web.search", {"query": "x"}))
            assert env["error"]["code"] == "UNKNOWN_TOOL"

            env = _env(
                await client.call_tool("invoice.extract", {"path": "請求書.pdf", "schema": "invoice.v1"})
            )
            check_envelope("invoice.extract", env)
            assert env["data"]["needs_review"] is False
            assert [(p.metadata or {}).get("purpose") for p in seen] == ["ocr", "invoice_extract"]

            env = _env(await client.call_tool("pdf.info", {"path": "鍵.pdf", "password_ref": "pw-3"}))
            assert env["error"]["code"] == "PASSWORD_REQUIRED"
            env = _env(
                await client.call_tool(
                    "pdf.info",
                    {"path": "鍵.pdf", "password_ref": "pw-3"},
                    meta={PASSWORDS_META_KEY: {"pw-3": "secret"}},
                )
            )
            assert env["ok"] and env["data"]["encrypted"] is True

    anyio.run(scenario)


def test_without_sampling_capability_ocr_fails_cleanly(ws: Path) -> None:
    make_pdf(ws / "a.pdf")

    async def scenario() -> None:
        async with create_connected_server_and_client_session(build_server()) as client:
            env = _env(await client.call_tool("pdf.ocr", {"path": "a.pdf", "pages": "4"}))
            assert env["error"]["code"] == "TOOL_FAILED"

    anyio.run(scenario)


def test_stdio_server(ws: Path) -> None:
    make_xlsx(ws / "b.xlsx")
    src = Path(__file__).resolve().parents[1] / "src"
    env_vars = {
        **os.environ,
        "OFFICE_TOOLS_WORKSPACE": str(ws),
        "PYTHONPATH": str(src),
        "PYTHONIOENCODING": "utf-8",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "office_tools.server"], env=env_vars)

    async def scenario() -> None:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert len(tools.tools) == 32
            env = _env(await client.call_tool("xlsx.read_range", {"path": "b.xlsx", "range": "明細!A1:B2"}))
            check_envelope("xlsx.read_range", env)
            assert env["data"]["rows"][1][1] == "XX Shipping"

    anyio.run(scenario)
