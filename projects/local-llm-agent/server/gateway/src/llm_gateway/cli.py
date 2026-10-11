"""`llm-gateway serve|create-user|keygen|migrate`。

秘密情報は設定ファイルに書かず、環境変数かファイルパスで渡す:
- LLMGW_DATABASE_URL                       DB の URL（パスワードを含む）
- LLMGW_JWT_PRIVATE_KEY / _FILE            JWT の署名鍵（Ed25519, PEM）
- LLMGW_PAYLOAD_KEYS / _FILE               ペイロードの暗号鍵（JSON: {"active","keys"}）
- LLMGW_NEW_PASSWORD                       create-user の初期パスワード（省略時は入力を求める）
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from .config import load_profile, load_settings
from .crypto import generate_ed25519_pem, generate_payload_keys, load_payload_cipher, load_signing_key


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app
    from .gateway import Gateway

    settings = load_settings(args.config)
    profile = load_profile(settings.resolve(settings.profile) if settings.profile else None)
    gw = Gateway(
        settings,
        profile=profile,
        signing_key=load_signing_key(settings),
        cipher=load_payload_cipher(settings),
    )
    app = create_app(gw)
    srv = settings.server
    uvicorn.run(
        app,
        host=args.host or srv.host,
        port=args.port or srv.port,
        ssl_certfile=str(settings.resolve(srv.tls_cert_file)) if srv.tls_cert_file else None,
        ssl_keyfile=str(settings.resolve(srv.tls_key_file)) if srv.tls_key_file else None,
        log_level=args.log_level,
        access_log=False,  # アクセスログは URL のみだが、クエリに本文が入る経路を作らないため止める
    )
    return 0


async def _create_user(args: argparse.Namespace, password: str) -> str:
    from .auth import LocalAuthProvider
    from .db import Database, make_engine
    from .migrate import upgrade
    from .users import UserAdmin, UserCreate
    from .util import ApiError, Clock

    settings = load_settings(args.config)
    profile = load_profile(settings.resolve(settings.profile) if settings.profile else None)
    db = Database(make_engine(settings.database_url()))
    try:
        if settings.database.auto_migrate:
            await upgrade(db.engine)
        admin = UserAdmin(LocalAuthProvider(settings.auth, Clock()), settings.auth, profile, Clock())
        body = UserCreate(
            login=args.login,
            display_name=args.display_name or args.login,
            password=password,
            role=args.role,
            department=args.department,
            max_concurrent=args.max_concurrent,
            net_level_cap=args.net_level_cap,
        )
        try:
            async with db.tx() as s:
                user = await admin.create(s, body)
                return user.id
        except ApiError as e:
            raise SystemExit(f"error: {e.message}") from None
    finally:
        await db.dispose()


def cmd_create_user(args: argparse.Namespace) -> int:
    password = os.environ.get("LLMGW_NEW_PASSWORD")
    if not password:
        password = getpass.getpass("初期パスワード: ")
        if password != getpass.getpass("もう一度: "):
            print("パスワードが一致しません", file=sys.stderr)
            return 2
    uid = asyncio.run(_create_user(args, password))
    print(f"created user id={uid} login={args.login}")
    return 0


def cmd_keygen(args: argparse.Namespace) -> int:
    out: Path = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    jwt_path = out / "jwt_ed25519.pem"
    keys_path = out / "payload_keys.json"
    written: list[Path] = []
    if args.rotate:
        # 暗号鍵のローテーション: 古い鍵は残し（その鍵のペイロードが消えるまで必要）、新しい鍵を有効にする
        data = json.loads(keys_path.read_text(encoding="utf-8"))
        if args.key_id in data["keys"]:
            print(f"key_id {args.key_id} は既にあります。別の --key-id を指定してください", file=sys.stderr)
            return 2
        new_key = generate_payload_keys(args.key_id)["keys"]
        assert isinstance(new_key, dict)
        data["keys"].update(new_key)
        data["active"] = args.key_id
        keys_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        written.append(keys_path)
    else:
        for p in (jwt_path, keys_path):
            if p.exists() and not args.force:
                print(f"既にあります（--force で上書き）: {p}", file=sys.stderr)
                return 2
        jwt_path.write_bytes(generate_ed25519_pem())
        keys_path.write_text(json.dumps(generate_payload_keys(args.key_id), indent=2), encoding="utf-8")
        written += [jwt_path, keys_path]
    for p in written:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    print(f"JWT 署名鍵:   {jwt_path}  → LLMGW_JWT_PRIVATE_KEY_FILE に設定")
    print(f"暗号鍵:       {keys_path} → LLMGW_PAYLOAD_KEYS_FILE に設定")
    print("いずれも DB とは別の場所に置き、root（サービスの実行ユーザー）だけが読めるようにしてください。")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from .db import make_engine
    from .migrate import upgrade

    settings = load_settings(args.config)

    async def run() -> None:
        engine = make_engine(settings.database_url())
        try:
            await upgrade(engine)
        finally:
            await engine.dispose()

    asyncio.run(run())
    print("migrated to head")
    return 0


def cmd_openapi(args: argparse.Namespace) -> int:
    """OpenAPI の定義を書き出す（local_llm_api.md: server/gateway/openapi.json としてコミットする）。"""
    from .app import create_app
    from .config import ModelSettings, Profile, Settings
    from .crypto import PayloadCipher, parse_ed25519_pem
    from .gateway import Gateway

    settings = Settings(
        database={"url": "sqlite+aiosqlite:///:memory:"},  # type: ignore[arg-type]
        models=[ModelSettings(id="local-12b", label="高速", engine={"kind": "fake"})],  # type: ignore[arg-type]
    )
    gw = Gateway(
        settings,
        profile=Profile(),
        signing_key=parse_ed25519_pem(generate_ed25519_pem()),
        cipher=PayloadCipher({"k": os.urandom(32)}, "k"),
    )
    spec = create_app(gw).openapi()
    text = json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
    Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="llm-gateway", description="local-llm-agent gateway")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="Gateway を起動する")
    sp.add_argument("--config", required=True, type=Path)
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--log-level", default="info")
    sp.set_defaults(fn=cmd_serve)

    cu = sub.add_parser("create-user", help="利用者を作る（初期パスワードは LLMGW_NEW_PASSWORD か入力）")
    cu.add_argument("--config", required=True, type=Path)
    cu.add_argument("--login", required=True)
    cu.add_argument("--display-name", default=None)
    cu.add_argument("--role", choices=["user", "admin"], default="user")
    cu.add_argument("--department", default=None)
    cu.add_argument("--max-concurrent", type=int, default=2)
    cu.add_argument("--net-level-cap", choices=["off", "search", "browse"], default="off")
    cu.set_defaults(fn=cmd_create_user)

    kg = sub.add_parser("keygen", help="JWT の署名鍵（Ed25519）とペイロードの暗号鍵（AES-256）を作る")
    kg.add_argument("--out-dir", required=True, type=Path)
    kg.add_argument("--key-id", default="k1")
    kg.add_argument(
        "--rotate", action="store_true", help="既存の payload_keys.json に新しい鍵を足して有効にする"
    )
    kg.add_argument("--force", action="store_true")
    kg.set_defaults(fn=cmd_keygen)

    mg = sub.add_parser("migrate", help="DB を最新のスキーマにする（Alembic）")
    mg.add_argument("--config", required=True, type=Path)
    mg.set_defaults(fn=cmd_migrate)

    oa = sub.add_parser("openapi", help="OpenAPI の定義（openapi.json）を書き出す")
    oa.add_argument("--out", required=True, type=Path)
    oa.set_defaults(fn=cmd_openapi)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
