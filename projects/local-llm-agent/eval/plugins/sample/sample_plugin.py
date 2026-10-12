"""プラグイン（AGT-09）の見本と試験用: MCP（stdio）のサーバー。

ツールの注釈（_meta の x-agent-*）で、Runtime に効果（read / write）とパスの引数を伝える。
注釈が無いツールは「書き込み」とみなされ、毎回承認が必要になる（安全側）。

    uv run python eval/plugins/sample/sample_plugin.py
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sample")


@mcp.tool(
    description="テキストファイルの文字数を数える",
    meta={"x-agent-effect": "read", "x-agent-path-args": ["/path"], "x-agent-idempotent": True},
)
def word_count(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8")
    return f"{Path(path).name}: {len(text)} 文字"


@mcp.tool(
    description="確認済みの印を書いたテキストファイルを作る",
    meta={"x-agent-effect": "write", "x-agent-path-args": ["/path"]},
)
def stamp(path: str, text: str) -> str:
    Path(path).write_text(f"STAMP: {text}\n", encoding="utf-8", newline="\n")
    return f"{Path(path).name} に印を書きました"


if __name__ == "__main__":
    mcp.run()
