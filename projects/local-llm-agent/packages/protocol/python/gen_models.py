"""protocol.schema.json から pydantic v2 モデルを生成する（--check で差分検査）。

正本は packages/protocol/schemas/src/*.yaml。先に `node scripts/gen.mjs` を実行すること。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUFF_CONFIG = HERE.parents[2] / "pyproject.toml"  # リポジトリの設定で整形し、出力を環境に依存させない
SCHEMA = HERE / "src" / "lla_protocol" / "data" / "protocol.schema.json"
OUT = HERE / "src" / "lla_protocol" / "generated" / "models.py"
HEADER = "# 生成物（packages/protocol/python/gen_models.py）。編集しないこと。\n"


def generate(dest: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(SCHEMA),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(dest),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.12",
        "--use-standard-collections",
        "--use-union-operator",
        "--use-schema-description",
        "--field-constraints",
        "--disable-timestamp",
        "--use-title-as-name",
        "--collapse-root-models",
        "--formatters",
        "ruff-format",
    ]
    subprocess.run(cmd, check=True)  # noqa: S603 - 固定の引数で自分のモジュールを実行する
    fmt = [sys.executable, "-m", "ruff", "format", "--quiet", "--config", str(RUFF_CONFIG), str(dest)]
    subprocess.run(fmt, check=True)  # noqa: S603
    dest.write_text(HEADER + dest.read_text(encoding="utf-8"), encoding="utf-8")


def main() -> int:
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_out = Path(tmp) / "models.py"
            generate(tmp_out)
            if not OUT.exists() or OUT.read_text(encoding="utf-8") != tmp_out.read_text(encoding="utf-8"):
                print(f"生成物が古いです: {OUT}", file=sys.stderr)
                return 1
        print("checked: models.py")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    (OUT.parent / "__init__.py").touch()
    generate(OUT)
    print(f"generated: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
