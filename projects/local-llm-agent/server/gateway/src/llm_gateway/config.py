"""Gateway の設定（YAML）と会社プロファイル（design §9）。

秘密情報（DB のパスワード・JWT の署名鍵・ペイロードの暗号鍵）は設定ファイルに書かない。
環境変数かファイルパスで渡す（`secrets.py`）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

NetLevel = Literal["off", "search", "browse"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_Strict):
    host: str = "0.0.0.0"  # noqa: S104 - サーバーとして待ち受ける
    port: int = 8443
    tls_cert_file: str | None = None
    tls_key_file: str | None = None


class DatabaseSettings(_Strict):
    # パスワードを含む URL は環境変数 LLMGW_DATABASE_URL で渡す（こちらが優先）
    url: str = "postgresql+asyncpg://llmgw@localhost:5432/llmgw"
    auto_migrate: bool = True
    pool_size: int = 10


class EngineSettings(_Strict):
    kind: Literal["vllm", "fake"] = "vllm"
    base_url: str | None = None
    served_model: str | None = None  # vLLM の --served-model-name（LoRA 名でもよい）
    # 構造化出力の渡し方は vLLM の版で違う（design §6.2）:
    #   guided_json        … 旧来の extra body `guided_json`
    #   json_schema        … OpenAI 互換 `response_format: {type: json_schema}`
    #   structured_outputs … 新しい版の `structured_outputs: {json: ...}`
    guided_mode: Literal["guided_json", "json_schema", "structured_outputs"] = "guided_json"
    timeout_s: float = 600.0
    connect_timeout_s: float = 5.0
    api_key_env: str | None = None  # vLLM に --api-key を付けた場合、その値を入れた環境変数名
    # fake のみ: 決め打ちの応答
    fake_output: str | None = None
    fake_token_delay_s: float = 0.0
    fake_chunk_chars: int = 12


class ModelSettings(_Strict):
    id: str
    label: str
    context_budget: int = 32768
    lora: str | None = None
    # 画像を読めるモデルか（DOC-07 の画像の説明、Gemma 4 は読める）
    vision: bool = False
    engine: EngineSettings = Field(default_factory=EngineSettings)


class SchedulerSettings(_Strict):
    max_concurrent: int = 5  # 全体の同時実行数（vLLM の max_num_seqs に合わせる）
    interactive_max_input_tokens: int = 4096  # これを超える interactive は normal に下げる
    eta_window: int = 50  # eta の平均を取る直近のジョブ数
    default_job_seconds: float = 5.0  # 実績がないときの平均処理時間
    max_queued_per_user: int = 50  # 利用者ごとの待ち＋実行中の上限（超えたら 429 QUEUE_LIMIT）
    max_requeues: int = 10  # エンジン障害での再投入の上限（超えたら ENGINE_UNAVAILABLE で失敗）
    engine_retry_s: float = 5.0  # エンジン停止中に再試行する間隔
    tick_s: float = 1.0


class JobSettings(_Strict):
    result_ttl_s: float = 600.0  # 完了後に応答を残す時間（R4: 10分）
    ping_interval_s: float = 15.0
    chars_per_token: float = 1.5
    max_schema_bytes: int = 65536
    cleanup_interval_s: float = 60.0
    token_flush_ms: float = 50.0  # トークンイベントをまとめて書き込む間隔


class Argon2Settings(_Strict):
    memory_kib: int = 65536  # 64 MiB（ADR-008）
    time_cost: int = 3
    parallelism: int = 1


class AuthSettings(_Strict):
    issuer: str = "llm-gateway"
    access_ttl_s: int = 900
    refresh_ttl_days: int = 30
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    min_password_length: int = 8
    argon2: Argon2Settings = Field(default_factory=Argon2Settings)


class SecretSettings(_Strict):
    """秘密情報は「ファイルのパス」だけを書ける。値そのものは環境変数で渡す。"""

    jwt_private_key_file: str | None = None
    payload_keys_file: str | None = None


class UpstreamSettings(_Strict):
    rag_url: str | None = None
    web_relay_url: str | None = None
    ocr_url: str | None = None
    timeout_s: float = 60.0
    # 上流と共有する内部トークン。値は環境変数（LLMGW_<KIND>_SECRET）か、ファイルのパスで渡す
    rag_secret_file: str | None = None
    web_relay_secret_file: str | None = None
    ocr_secret_file: str | None = None

    def secret(self, kind: str, base_dir: str = ".") -> str | None:
        """kind: rag | web_relay | ocr。環境変数を優先し、無ければファイルから読む。"""
        env = os.environ.get(f"LLMGW_{kind.upper()}_SECRET")
        if env:
            return env
        path = getattr(self, f"{kind}_secret_file")
        if not path:
            return None
        p = Path(path) if Path(path).is_absolute() else Path(base_dir) / path
        return p.read_text(encoding="utf-8").strip()


class MetricsSettings(_Strict):
    allow_cidrs: list[str] = Field(
        default_factory=lambda: ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    )
    # ダッシュボードの GPU（ADM-03）。dcgm-exporter の /metrics。無ければ GPU は「取得できません」
    dcgm_url: str | None = None


class Settings(_Strict):
    server: ServerSettings = Field(default_factory=ServerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    profile: str | None = None  # 会社プロファイル（profile.yaml）のパス。設定ファイルからの相対
    models: list[ModelSettings] = Field(default_factory=list)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    jobs: JobSettings = Field(default_factory=JobSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    secrets: SecretSettings = Field(default_factory=SecretSettings)
    upstreams: UpstreamSettings = Field(default_factory=UpstreamSettings)
    metrics: MetricsSettings = Field(default_factory=MetricsSettings)
    admin_web_dir: str | None = None  # 管理画面（apps/admin-web の dist）。あれば /admin で配る
    base_dir: str = "."  # 相対パスの基準（読み込み時に設定ファイルのフォルダを入れる）

    def database_url(self) -> str:
        return os.environ.get("LLMGW_DATABASE_URL") or self.database.url

    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else Path(self.base_dir) / p


def load_settings(path: Path) -> Settings:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"設定ファイルの形式が正しくありません: {path}")
    data.setdefault("base_dir", str(path.parent.resolve()))
    return Settings.model_validate(data)


# ---- 会社プロファイル ----


class OrgInfo(_Strict):
    id: str
    name: str


class Macro(_Strict):
    """定型作業（スラッシュコマンド。APP-09）。"""

    name: str
    description: str = ""
    prompt: str


class InstructionsSpec(_Strict):
    version: int = 1
    body: str | None = None
    file: str | None = None


class NetDefaults(_Strict):
    level_cap: NetLevel = "off"
    monthly_search_limit: int | None = 300


class FeatureFlags(_Strict):
    rag: bool = True
    ocr: bool = True
    web: bool = True


class Profile(_Strict):
    org: OrgInfo = Field(default_factory=lambda: OrgInfo(id="dev", name="開発用"))
    instructions: InstructionsSpec = Field(default_factory=InstructionsSpec)
    escalation_target: str | None = None
    net: NetDefaults = Field(default_factory=NetDefaults)
    invoice_schemas: list[str] = Field(default_factory=lambda: ["invoice.v1"])
    # ここで false にした機能は、上流が設定されていても使わせない
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    macros: list[Macro] = Field(default_factory=list)
    macros_dir: str | None = None
    # 読み込み後に埋める
    instructions_body: str = ""
    base_dir: str = "."


def load_profile(path: Path | None) -> Profile:
    if path is None:
        return Profile()
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"プロファイルの形式が正しくありません: {path}")
    base = path.parent.resolve()
    data["base_dir"] = str(base)
    prof = Profile.model_validate(data)
    ins = prof.instructions
    if ins.body is not None:
        prof.instructions_body = ins.body
    elif ins.file is not None:
        prof.instructions_body = (base / ins.file).read_text(encoding="utf-8")
    if prof.macros_dir is not None:
        mdir = base / prof.macros_dir
        for f in sorted(mdir.glob("*.yaml")) + sorted(mdir.glob("*.yml")):
            raw: Any = yaml.safe_load(f.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else [raw]
            prof.macros.extend(Macro.model_validate(item) for item in items if item)
    return prof
