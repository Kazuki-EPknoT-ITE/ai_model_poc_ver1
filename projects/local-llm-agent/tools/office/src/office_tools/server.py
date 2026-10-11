"""office-tools の MCP stdio サーバー。

- tools/list: tools.json の office-tools 担当分。inputSchema は args_schema、
  ToolSpec の残りの項目は `annotations` の `x-agent-*` キー（protocol §2.2）。
- tools/call: ToolResult の封筒を structuredContent と content(text) の両方に入れる。
- OCR・帳票抽出の推論は MCP sampling（sampling/createMessage）でクライアントに依頼する。
- パスワード: tools/call の `_meta["lla/passwords"]`（{password_ref: password}）で受け取る。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError

from office_tools import tools as _tools  # noqa: F401  (ツールを登録する)
from office_tools.envelope import ContinuationStore, dumps
from office_tools.errors import ToolFailure
from office_tools.registry import SamplingRequest, ToolContext, call, office_specs

PASSWORDS_META_KEY = "lla/passwords"
SAMPLING_TIMEOUT_S = 280.0

log = logging.getLogger("office_tools")


def tool_annotations(spec: dict[str, Any]) -> types.ToolAnnotations:
    effect = spec["effect"]
    return types.ToolAnnotations.model_validate(
        {
            "readOnlyHint": effect == "read",
            "destructiveHint": effect in ("delete",),
            "idempotentHint": bool(spec["idempotent"]),
            "openWorldHint": effect == "net",
            "x-agent-effect": effect,
            "x-agent-path-args": list(spec["path_args"]),
            "x-agent-idempotent": bool(spec["idempotent"]),
            "x-agent-untrusted-output": bool(spec["untrusted_output"]),
            "x-agent-paginated": bool(spec.get("paginated", False)),
            "x-agent-timeout-ms": int(spec["timeout_ms"]),
            "x-agent-priority": spec["priority"],
        }
    )


def list_tool_defs() -> list[types.Tool]:
    return [
        types.Tool(
            name=spec["name"],
            description=spec["description"],
            inputSchema=spec["args_schema"],
            annotations=tool_annotations(spec),
        )
        for spec in office_specs()
    ]


class McpSampler:
    """SamplingRequest → sampling/createMessage。応答は text content を期待する。"""

    def __init__(self, session: ServerSession) -> None:
        self._session = session

    async def __call__(self, request: SamplingRequest) -> str:
        content: list[types.SamplingMessageContentBlock] = []
        if request.image_png is not None:
            content.append(types.ImageContent(type="image", data=request.image_b64(), mimeType="image/png"))
        if request.text is not None:
            content.append(types.TextContent(type="text", text=request.text))
        message = types.SamplingMessage(role="user", content=content[0] if len(content) == 1 else content)
        metadata = {"purpose": request.purpose, **request.metadata}
        try:
            with anyio.fail_after(SAMPLING_TIMEOUT_S):
                result = await self._session.create_message(
                    [message],
                    max_tokens=request.max_tokens,
                    system_prompt=request.system_prompt,
                    temperature=0.0,
                    metadata=metadata,
                )
        except TimeoutError as e:
            raise ToolFailure("TIMEOUT", "推論の応答がタイムアウトしました") from e
        except McpError as e:
            raise ToolFailure(
                "TOOL_FAILED",
                f"この操作は失敗しました。理由: 推論の依頼が拒否されました（{e.error.message[:200]}）",
            ) from e
        if not isinstance(result.content, types.TextContent):
            raise ToolFailure(
                "TOOL_FAILED", "この操作は失敗しました。理由: 推論の応答がテキストではありません"
            )
        return result.content.text


def _passwords(meta: types.RequestParams.Meta | None) -> dict[str, str]:
    if meta is None or not meta.model_extra:
        return {}
    raw = meta.model_extra.get(PASSWORDS_META_KEY)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def build_server(store: ContinuationStore | None = None) -> Server[Any, Any]:
    server: Server[Any, Any] = Server("office-tools", version="0.1.0")
    continuations = store or ContinuationStore()

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def _list_tools() -> list[types.Tool]:
        return list_tool_defs()

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        rc = server.request_context
        session = rc.session
        sampler = None
        if session.check_client_capability(types.ClientCapabilities(sampling=types.SamplingCapability())):
            sampler = McpSampler(session)
        ctx = ToolContext(store=continuations, sampler=sampler, passwords=_passwords(rc.meta))
        env = await call(ctx, name, arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=dumps(env))],
            structuredContent=env,
            isError=not env["ok"],
        )

    return server


async def serve_stdio() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    anyio.run(serve_stdio)


if __name__ == "__main__":
    main()
