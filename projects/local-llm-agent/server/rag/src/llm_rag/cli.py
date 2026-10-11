"""`llm-rag ingest|serve|search|eval --config rag.yaml`。

秘密情報は環境変数で渡す:
  LLM_RAG_SECRET   Gateway と共有する内部トークン（serve で必須。名前は server.secret_env で変えられる）
  QDRANT_API_KEY   Qdrant の API キー（qdrant.api_key_env で指定した場合）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from llm_rag.config import RagSettings, load_settings
from llm_rag.index import RagIndex


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _load(args: argparse.Namespace) -> RagSettings:
    return load_settings(args.config)


def cmd_ingest(args: argparse.Namespace) -> int:
    s = _load(args)
    if not s.sources:
        print("sources を設定してください", file=sys.stderr)
        return 2
    from llm_rag.ingest import run_ingest

    stats = run_ingest(RagIndex(s))
    print(json.dumps(stats.as_dict(), ensure_ascii=False))
    return 1 if stats.failed and not (stats.added or stats.updated or stats.unchanged) else 0


def cmd_serve(args: argparse.Namespace) -> int:
    s = _load(args)
    secret = os.environ.get(s.server.secret_env)
    if not secret:
        print(f"{s.server.secret_env} を設定してください", file=sys.stderr)
        return 2
    import uvicorn

    from llm_rag.app import create_app
    from llm_rag.search import Searcher

    app = create_app(Searcher(RagIndex(s)), secret=secret)
    uvicorn.run(app, host=args.host or s.server.host, port=args.port or s.server.port, log_level="info")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """開発・運用の確認用（結果は標準出力にだけ出す）。"""
    from llm_rag.search import Searcher

    s = _load(args)
    hits = Searcher(RagIndex(s)).search(
        args.query, args.groups.split(","), top_k=args.top_k, path_prefix=args.path_prefix
    )
    print(json.dumps({"hits": [h.__dict__ for h in hits]}, ensure_ascii=False, indent=2))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from llm_rag.evaluate import load_items, load_users, recall_at_k
    from llm_rag.search import Searcher

    s = _load(args)
    users = load_users(Path(args.users)) if args.users else None
    items = load_items(Path(args.items), users)
    searcher = Searcher(RagIndex(s))
    res = recall_at_k(items, lambda q, g, k: searcher.search(q, g, top_k=k), k=args.k)
    out = json.dumps(res.as_dict(), ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    print(out)
    return 0 if res.recall >= args.target else 1


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="llm-rag")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="フォルダを走査して差分を取り込む")
    p.add_argument("--config", required=True)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("serve", help="内部 API を起動する")
    p.add_argument("--config", required=True)
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("search", help="確認用の検索")
    p.add_argument("--config", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--groups", required=True, help="カンマ区切りの acl_groups")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--path-prefix")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("eval", help="Recall@k を計算する（試験仕様 rag_qa）")
    p.add_argument("--config", required=True)
    p.add_argument("--items", required=True, help="JSONL（id, question, acl_user|acl_groups, evidence）")
    p.add_argument("--users", help="acl_user → acl_groups の YAML")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--target", type=float, default=0.9)
    p.add_argument("--out")
    p.set_defaults(func=cmd_eval)

    args = ap.parse_args(argv)
    _setup_logging()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
