# 推論サーバー構築手順書

2026年10月11日 · v0.1（基本設計書 §6 に対応）

検証機（ZOTAC MAGNUS ONE ULTRA / RTX 5080 16GB）に推論サーバーを構築する手順。本番機（RTX 5090 32GB）でも同じ手順を使い、違いは §5.4 の起動パラメータだけにする。OS は未定のため **Ubuntu 24.04 LTS を主手順**とし、Windows 11 + WSL2 で構築する場合の差分を付録Aにまとめる。

- ソフトウェアの版のうち本書で確定できないものは「**M0で確定**」と書く。確定した版は `projects/local-llm-agent/server/deploy/VERSIONS.md`（§7.4 のテンプレート）に記録する。
- `<...>` はその環境に合わせて置き換える値。
- 作業は開発PCから SSH で行う想定。

---

## 1. 前提・調達チェック

| 項目 | 要件 | 確認 |
| --- | --- | --- |
| メモリ | DDR5 SO-DIMM 48GB×2 = 96GB（26B を CPU に退避させる評価と RAG の索引に使う） | [ ] |
| SSD | NVMe 2TB 以上（PCIe 5.0 スロットを OS とモデル用に）。可能なら PCIe 4.0 スロットにデータ・バックアップの一時置き場として2枚目を付ける | [ ] |
| UPS | 850W 電源の高負荷に耐えるもの（目安 1500VA 以上）。USB で接続して停電時に自動シャットダウンさせる（OPS-06） | [ ] |
| ネットワーク | **有線必須**（5GbE ポートを使う）。Wi-Fi は無効にする | [ ] |
| IP アドレス | 固定IP（またはDHCPで予約）: `<192.168.x.10>` | [ ] |
| ホスト名 | `<llm-sv01>`（DNS に A レコードを登録する。TLS 証明書の SAN と一致させる） | [ ] |
| 予備機 | 本番機が動き始めたら、検証機を予備機・学習機にする（OPS-03）。ホスト名 `<llm-sv02>` | [ ] |
| 外向き通信 | OS・ドライバの更新、モデルの取得、Web Relay の検索APIに必要。プロキシがあれば `<http://proxy:8080>` | [ ] |
| NAS | バックアップ先の共有 `<//nas01/llm-backup>` と、サービス用アカウント | [ ] |
| BIOS | Resizable BAR を有効、電源断から復帰したら自動で電源オン（AC Power Recovery = On）、Secure Boot は §3 の手順に合わせる | [ ] |

---

## 2. OS のインストールと初期設定（Ubuntu 24.04 LTS）

### 2.1 インストール
- Ubuntu Server 24.04 LTS（最小構成）を入れる。GUI は入れない。
- ディスク構成: `/` 200GB、`/srv` に残り全部（ext4 または xfs）。LVM を使う。
- インストール時に OpenSSH Server を選ぶ。

### 2.2 ユーザーと SSH

```bash
# 運用ユーザー（例 llmadmin）を sudo グループに入れる（インストール時に作っていれば省略）
sudo adduser llmadmin && sudo usermod -aG sudo llmadmin

# 開発PCで鍵を作って登録する（開発PCの PowerShell で実行）
#   ssh-keygen -t ed25519 -C "llm-sv01"
#   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh llmadmin@<llm-sv01> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

# パスワードでのログインと root ログインを禁止する
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
EOF
sudo systemctl restart ssh
```

### 2.3 ファイアウォール（ufw）

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <192.168.0.0/16> to any port 22 proto tcp     # SSH（社内LANのみ）
sudo ufw allow from <192.168.0.0/16> to any port 443 proto tcp    # Gateway（クライアント・管理画面）
sudo ufw allow from <管理PCのIP> to any port 3000 proto tcp        # Grafana（管理者のみ）
sudo ufw enable
```

> Docker は公開ポートについて ufw を素通りする。そのため compose ではポートを `<サーバーIP>:443:8443` のように**特定のIPにだけ**公開し、外向き通信の制御は §5.3 の `DOCKER-USER` チェーンで行う。

### 2.4 時刻同期・基本パッケージ

```bash
sudo timedatectl set-timezone Asia/Tokyo
sudo timedatectl set-ntp true                 # 社内NTPがあれば /etc/systemd/timesyncd.conf の NTP= に設定する
sudo apt update && sudo apt -y upgrade
sudo apt -y install build-essential curl git jq htop nvtop tmux unzip ca-certificates gnupg lm-sensors nut cifs-utils
```

### 2.5 自動更新の方針
- `unattended-upgrades` は**セキュリティ更新だけ**を自動で適用し、自動再起動はしない。
- **NVIDIA ドライバ・CUDA・カーネルは自動更新から外して固定する**（版が変わると vLLM が動かなくなるおそれがあるため）。更新は検証機で確認してから本番機に適用する。

```bash
sudo apt -y install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
# ドライバとカーネルを固定する（§3 でドライバを入れた後に実行）
sudo apt-mark hold 'nvidia-*' 'libnvidia-*' 'linux-image-*' 'linux-headers-*' 'linux-generic*'
```

---

## 3. NVIDIA ドライバ・CUDA・Container Toolkit・Docker

### 3.1 ドライバ（RTX 50 シリーズ＝Blackwell）
- RTX 50 シリーズは **R570 系以降のドライバ**が必要で、カーネルモジュールは**オープン版（`-open`）だけ**が対応している。プロプライエタリ版のカーネルモジュールでは認識されない。
- 使う版は **M0で確定**する。vLLM・PyTorch が要求する CUDA の版を満たすものを選ぶ。

```bash
# 利用できるドライバを確認する
ubuntu-drivers list --gpgpu
# オープン版を入れる（<ver> は M0 で確定。例: 570 以降の -open 付きパッケージ）
sudo apt -y install nvidia-driver-<ver>-open
sudo reboot
# 再起動後に確認する
nvidia-smi        # GPU 名・ドライバ版・CUDA 版（ドライバが対応する上限）が表示されること
```

- Secure Boot が有効な場合は、インストール時に MOK の登録を求められる。再起動時のコンソールで登録するか、BIOS で Secure Boot を無効にする（どちらにしたかを VERSIONS.md に記録する）。

### 3.2 CUDA
推論と学習はコンテナ（vLLM イメージ、学習用イメージ）に入った CUDA を使うので、**ホストには CUDA Toolkit を入れなくてよい**。ホストで llama.cpp をビルドする場合（§5.5）だけ、Blackwell に対応した CUDA Toolkit（**12.8 以降。版は M0 で確定**）を入れる。

```bash
# NVIDIA の公式リポジトリ（cuda-keyring）を追加してから入れる
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb && sudo apt update
sudo apt -y install cuda-toolkit-<12-x>     # M0で確定
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
```

### 3.3 Docker Engine

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker llmadmin   # 一度ログアウトすると反映される
```

ログの肥大化を防ぐ設定:

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{ "log-driver": "json-file", "log-opts": { "max-size": "50m", "max-file": "5" } }
EOF
sudo systemctl restart docker
```

### 3.4 NVIDIA Container Toolkit

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt -y install nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
# 確認する（イメージのタグは M0 で確定。ドライバが対応する CUDA の版以下にする）
docker run --rm --gpus all nvidia/cuda:<12.x>-base-ubuntu24.04 nvidia-smi
```

---

## 4. ディレクトリ構成とモデルの取得

### 4.1 ディレクトリ

```
/srv/llm/
  models/            ← モデル重み（読み取り専用でコンテナにマウントする）
    base/<model>/<rev>/          例: base/gemma-4-12b-it-qat-int4/<commit>/
    lora/<name>/<version>/       モデル台帳と対応させる（設計書 §8.4）
    gguf/<model>/                llama.cpp 評価用
  data/
    postgres/        ← Gateway の DB
    qdrant/          ← RAG 索引
    rag-staging/     ← 取り込みの作業領域
    prometheus/  grafana/
  deploy/            ← リポジトリの server/deploy を配置（git clone または rsync）
  secrets/           ← .env, TLS 鍵, キュー暗号鍵（chmod 700、バックアップは暗号化する）
  backups/           ← NAS へ送る前の一時置き場
  logs/
```

```bash
sudo mkdir -p /srv/llm/{models/{base,lora,gguf},data/{postgres,qdrant,rag-staging,prometheus,grafana},deploy,secrets,backups,logs}
sudo chown -R llmadmin:llmadmin /srv/llm
chmod 700 /srv/llm/secrets
```

### 4.2 モデルの取得
- Gemma 4 は Apache 2.0 ライセンス（要件書 3.2）。Hugging Face のモデルページで**利用条件への同意（ゲート）が求められる場合は**、組織のアカウントで同意してから取得する。
- 取得するリポジトリ ID とリビジョン（commit）は **M0で確定**し、VERSIONS.md に記録する。候補: 12B の QAT int4 版（公式）、12B の bf16 版（比較用）、26B A4B の int4 版、llama.cpp 用の GGUF Q4 版。

```bash
# 取得用の Python 環境（ホストに uv を入れる）
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install "huggingface_hub[cli]"
huggingface-cli login            # トークンは /srv/llm/secrets で管理し、作業が終わったら logout する

# リビジョンを固定して取得する
huggingface-cli download <org>/<gemma-4-12b-...> --revision <commit> \
  --local-dir /srv/llm/models/base/<gemma-4-12b-...>/<commit>
# 取得したファイルのハッシュを記録する
(cd /srv/llm/models/base/<gemma-4-12b-...>/<commit> && sha256sum * > SHA256SUMS)
```

**オフラインで配置する場合**（サーバーが外に出られない時期・本番機）: 開発PCなど外に出られる端末で同じコマンドで取得する → `tar` でまとめて USB SSD または NAS で運ぶ → サーバーで展開し、`sha256sum -c SHA256SUMS` で照合する。vLLM には `HF_HUB_OFFLINE=1` を設定し、ローカルパスを指定して起動する。

---

## 5. docker compose の構成

### 5.1 サービス一覧

| サービス | イメージ | ネットワーク | 公開ポート | GPU |
| --- | --- | --- | --- | --- |
| `gateway` | 自作（`server/gateway`） | `frontend`, `internal` | `<サーバーIP>:443` → 8443 | — |
| `postgres` | `postgres:16` | `internal` | なし | — |
| `vllm-12b` | `vllm/vllm-openai:<M0で確定>` | `internal` | なし | ○ |
| `vllm-26b` | 同上（本番機のみ。`profiles: [prod]`） | `internal` | なし | ○ |
| `rag` | 自作（`server/rag`。埋め込みモデルを同梱） | `internal` | なし | ○（小さい割り当て） |
| `qdrant` | `qdrant/qdrant:<M0で確定>` | `internal` | なし | — |
| `ocr` | 自作（`server/ocr`） | `internal` | なし | ○ |
| `web-relay` | 自作（`server/web-relay`） | `internal`, `egress` | なし | — |
| `prometheus` / `grafana` / `dcgm-exporter` | 公式イメージ | `internal`, `frontend`（grafana のみ） | `<サーバーIP>:3000`（grafana） | dcgm のみ |

### 5.2 ネットワーク
- `internal`: `internal: true`。**外向き通信ができない**。推論・RAG・OCR・DB はここだけに置く（NET-12）。
- `egress`: 外に出られるネットワーク。**web-relay だけ**を接続する。
- `frontend`: ポート公開用。gateway と grafana を接続する。公開ポートを持つには internal 以外のネットワークが必要なため、gateway も技術的には外へ出られてしまう。これを §5.3 の `DOCKER-USER` で塞ぐ。

### 5.3 外向き通信の制限（DOCKER-USER）
`egress` ネットワークのサブネットだけ外に出られるようにし、`frontend` から外への新規接続は落とす。

```bash
# compose で frontend=172.30.0.0/24, egress=172.31.0.0/24 に固定する（§5.4 の networks 定義）
sudo iptables -I DOCKER-USER -s 172.30.0.0/24 ! -d 192.168.0.0/16 -m conntrack --ctstate NEW -j DROP
sudo iptables -I DOCKER-USER -s 172.31.0.0/24 -d 192.168.0.0/16 -j DROP   # web-relay から社内LANへの接続を禁止（SSRF対策の多重化）
sudo apt -y install iptables-persistent && sudo netfilter-persistent save
```

> 社内のアドレス帯（`192.168.0.0/16` など）は実際の値に置き換える。複数ある場合は行を増やす。

### 5.4 compose の骨子（`/srv/llm/deploy/compose.yaml`）

```yaml
name: llm
networks:
  internal: { internal: true }
  frontend: { ipam: { config: [{ subnet: 172.30.0.0/24 }] } }
  egress:   { ipam: { config: [{ subnet: 172.31.0.0/24 }] } }

x-gpu: &gpu
  deploy:
    resources:
      reservations:
        devices: [{ driver: nvidia, count: all, capabilities: [gpu] }]

services:
  postgres:
    image: postgres:16
    env_file: /srv/llm/secrets/postgres.env
    volumes: [ "/srv/llm/data/postgres:/var/lib/postgresql/data" ]
    networks: [ internal ]
    restart: unless-stopped
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U llm"], interval: 10s, retries: 5 }

  vllm-12b:
    image: vllm/vllm-openai:<M0で確定>
    <<: *gpu
    ipc: host
    environment:
      HF_HUB_OFFLINE: "1"
    volumes: [ "/srv/llm/models:/models:ro" ]
    networks: [ internal ]
    restart: unless-stopped
    command: >
      --model /models/base/<gemma-4-12b-qat-int4>/<rev>
      --served-model-name local-12b
      --max-model-len 32768
      --kv-cache-dtype fp8
      --gpu-memory-utilization 0.85
      --max-num-seqs 5
      --enable-lora --max-loras 4 --max-lora-rank 64
      --enable-prefix-caching
      --guided-decoding-backend xgrammar
      --port 8000
    healthcheck: { test: ["CMD", "curl", "-fs", "http://localhost:8000/health"], interval: 15s, retries: 20, start_period: 300s }

  gateway:
    image: llm/gateway:<tag>
    env_file: /srv/llm/secrets/gateway.env
    volumes: [ "/srv/llm/secrets/tls:/tls:ro", "/srv/llm/profiles:/profiles:ro" ]
    ports: [ "<サーバーIP>:443:8443" ]
    networks: [ frontend, internal ]
    depends_on: { postgres: { condition: service_healthy } }
    restart: unless-stopped

  web-relay:
    image: llm/web-relay:<tag>
    command: [ "web-relay", "--config", "/profiles/web-relay.yaml" ]
    env_file: /srv/llm/secrets/web-relay.env
    volumes: [ "/srv/llm/profiles:/profiles:ro", "/srv/llm/secrets/tls/ca.crt:/tls/ca.crt:ro", "/srv/llm/data/web-relay:/data" ]
    networks: [ internal, egress ]
    restart: unless-stopped
  # rag / qdrant / ocr / prometheus / grafana / dcgm-exporter も同じ書き方で定義する（本番機の vllm-26b は profiles: [prod]）
```

**Web Relay の機密語・サイトの取り込み**: 機密語辞書と禁止／信頼サイトは管理画面で編集し、Web Relay が
Gateway の内部 API（`GET /internal/v1/net-policy`、API 仕様 §9.2）から 60 秒ごとに取り込む。
設定例は `server/web-relay/examples/web-relay.yaml`（`policy_sync.gateway_url: https://gateway:8443`、`ca_file`）。

- `web-relay.env` の `WEB_RELAY_SECRET` と `gateway.env` の `LLMGW_WEB_RELAY_SECRET` は**同じ値**にする（16文字以上）。
- Gateway のサーバー証明書の SAN に、コンテナ名 `gateway` を入れておく（Relay は `https://gateway:8443` で照合する）。
- 取り込めるまで（起動直後や Gateway の停止が1時間を超えたとき）、Web 検索・閲覧は 503 で断られる。これは安全側の動作で、
  ログに `policy sync failed: <種類>` が出る。`/srv/llm/data/web-relay` は Relay の監査ログと利用回数の置き場。

**vLLM の起動パラメータの注意**
- 量子化: 公式の QAT int4 版（または AWQ/GPTQ 版）を指定すると、量子化方式はモデルの設定から自動で判別される。判別されない場合は `--quantization <方式>` を付ける。どの版を採るかは設計書 §8.2 の比較で決める。
- `--guided-decoding-backend` は vLLM の版によって引数名・指定方法が変わる（構造化出力の設定に統合されている版がある）。**M0 で使う版の書き方を確認し**、`guided_json` が効くことを §7.2 で必ず確かめる。
- `--max-num-seqs 5` は PRF-01 の目標値。16GB の検証機では、KV キャッシュが足りるかを §7.3 で実測してから決める。
- 本番機（5090）: `--max-model-len 65536`（PRF-04）。12B と 26B を同時に常駐させる場合は `--gpu-memory-utilization` を2つのインスタンスで分け合う（例 0.30 / 0.60。実測で決める。設計書 R5）。

### 5.5 26B A4B を CPU に退避させて評価する（検証機のみ、llama.cpp）
検証機ではサービスとして公開せず、精度を評価するためだけに使う（設計書 §6.2）。

```bash
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
git checkout <M0で確定したタグ>
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120   # Blackwell(RTX 50) のアーキテクチャ
cmake --build build -j --config Release

# MoE の専門家層を CPU に置き、残りを GPU に載せる（-ot の正規表現で指定する）
./build/bin/llama-server -m /srv/llm/models/gguf/<gemma-4-26b-a4b-Q4>.gguf \
  -c 32768 -ngl 99 -ot ".ffn_.*_exps.=CPU" \
  --host 127.0.0.1 --port 8081 -np 1
```

- 計測する項目: 生成速度（トークン/秒）、最初の文字が出るまでの時間、VRAM と RAM の使用量、評価セットの点数（`eval/harness` から OpenAI 互換 API で呼ぶ）。
- llama.cpp はリポジトリに含めず、タグとビルド設定を VERSIONS.md に記録する。

---

## 6. TLS 証明書（社内CA）

クライアントと Gateway の間は TLS にする（SEC-02）。社内CAを持っていない場合は、サーバー管理用の端末で小さな CA を作る。

```bash
# 1) 社内CA（初回だけ。鍵はオフラインで保管し、サーバーには置かない）
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
  -keyout ca.key -out ca.crt -subj "/O=<会社名>/CN=<会社名> Internal CA"

# 2) サーバー証明書（SAN にホスト名と IP を入れる）
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=<llm-sv01>.<domain>"
cat > san.ext <<'EOF'
subjectAltName=DNS:<llm-sv01>.<domain>,DNS:<llm-sv01>,IP:<192.168.x.10>
extendedKeyUsage=serverAuth
EOF
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -sha256 -extfile san.ext -out server.crt

# 3) サーバーに配置する
scp server.crt server.key llmadmin@<llm-sv01>:/srv/llm/secrets/tls/
```

**クライアントへの配布**（Windows の「信頼されたルート証明機関」に `ca.crt` を登録する）
- AD/GPO がある場合: GPO の「公開キーのポリシー > 信頼されたルート証明機関」で配布する。
- ない場合: 管理者権限の PowerShell で `Import-Certificate -FilePath ca.crt -CertStoreLocation Cert:\LocalMachine\Root`。アプリのインストーラに組み込むこともできる。
- 予備機（`<llm-sv02>`）にも同じ CA から証明書を発行する。有効期限はカレンダーで管理し、切れる30日前に更新する。

---

## 7. 動作確認

### 7.1 推論（vLLM に直接。サーバー内から確認する）

```bash
docker compose exec gateway curl -s http://vllm-12b:8000/v1/models | jq
docker compose exec gateway curl -s http://vllm-12b:8000/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{
    "model": "local-12b",
    "messages": [{"role":"user","content":"傭船契約とは何か、2文で説明してください。"}],
    "max_tokens": 200 }' | jq -r '.choices[0].message.content'
```

### 7.2 制約付き生成（guided_json）

```bash
docker compose exec gateway curl -s http://vllm-12b:8000/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{
    "model": "local-12b",
    "messages": [{"role":"user","content":"請求書フォルダの一覧を取得するアクションを返して"}],
    "guided_json": {
      "type":"object","required":["type","tool","args"],
      "properties":{
        "type":{"const":"tool_call"},
        "tool":{"const":"fs.list"},
        "args":{"type":"object","properties":{"path":{"type":"string","maxLength":200}},"required":["path"]}
      }},
    "max_tokens": 200 }' | jq -r '.choices[0].message.content' | jq .
```

合格条件: 10回実行して10回とも JSON として解釈でき、スキーマに合うこと。vLLM の版によって指定方法が変わる場合（`response_format: {type: "json_schema", ...}` など）は、その版の書き方を VERSIONS.md に記録する。

### 7.3 同時5件の簡易負荷

```bash
cat > /tmp/req.json <<'EOF'
{"model":"local-12b","messages":[{"role":"user","content":"海運業の精算業務の流れを800字で説明してください。"}],"max_tokens":800,"stream":false}
EOF
docker compose exec gateway sh -c '
  for i in 1 2 3 4 5; do
    ( s=$(date +%s.%N);
      curl -s http://vllm-12b:8000/v1/chat/completions -H "Content-Type: application/json" -d @- < /dev/stdin > /tmp/out$i.json;
      e=$(date +%s.%N); echo "req$i $(echo "$e - $s" | bc) s" ) < /tmp/req.json &
  done; wait'   # /tmp/req.json はコンテナ内にコピーしてから実行する
# 生成トークン数 / 秒 = usage.completion_tokens ÷ 経過秒（1件あたり）
```

- 本格的な測定（PRF-02/03: 最初の文字が出るまでの時間、1人あたりのトークン/秒）は `eval/harness` の負荷シナリオで行う。ここでは「5件同時に投げても落ちない・VRAM が溢れない」ことだけを確認する。
- 測定中は `watch -n1 nvidia-smi` で VRAM・温度・消費電力を見ておく。

### 7.4 VERSIONS.md の記録項目（テンプレート）

```markdown
# サーバー版数記録 — <ホスト名>（<検証機|本番機>）
記録日: YYYY-MM-DD / 記録者: <名前>

| 区分 | 項目 | 版・値 | 備考 |
| --- | --- | --- | --- |
| HW | GPU / VBIOS | RTX 5080 16GB / <nvidia-smi -q の VBIOS> | |
| HW | メモリ / SSD | 96GB / <型番> | |
| OS | ディストリ / カーネル | Ubuntu 24.04.x / <uname -r> | apt-mark hold 済み |
| GPU | ドライバ（-open） | <ver> | Secure Boot: <有効/無効> |
| GPU | CUDA（ホスト） | <ver または 未導入> | |
| コンテナ | Docker / Compose / NVIDIA CTK | <ver> | |
| 推論 | vLLM イメージ | vllm/vllm-openai:<tag>@<digest> | 構造化出力の指定方法: <...> |
| 推論 | llama.cpp | <tag> / CUDA_ARCH=120 | 評価専用 |
| モデル | 12B | <repo>@<commit> / 量子化: <方式> | SHA256SUMS 有 |
| モデル | 26B A4B | <repo>@<commit> / <方式> | |
| モデル | 埋め込み | <repo>@<commit> | |
| DB | PostgreSQL / Qdrant | 16.x / <tag> | |
| 起動設定 | vLLM 引数 | max-model-len=<>, max-num-seqs=<>, kv=fp8, gpu-mem=<> | |
| 実測 | 5件同時 | TTFT=<>s, <> tok/s/人, VRAM ピーク=<>GB | 条件: 入力<>tok, 出力<>tok |
| 既知の問題 | | <回避策> | |
```

---

## 8. 監視・バックアップ・自動起動

### 8.1 監視（OPS-05, ADM-03）
- Prometheus が収集するもの: vLLM の `/metrics`（待ち件数・実行中件数・KV使用率・トークン/秒）、`dcgm-exporter`（GPU温度・使用率・電力・VRAM）、Gateway の `/metrics`（キュー長・応答時間・エラー率）、node-exporter（CPU・メモリ・ディスク）。
- Grafana: 社内LANからだけ見られるようにする（ufw で管理PCに限定）。初期パスワードは `/srv/llm/secrets/grafana.env` で設定する。
- アラートの初期値（運用しながら調整する）:

| 条件 | 閾値 | 通知 |
| --- | --- | --- |
| GPU 温度 | 85℃ を5分超える | 管理者にメール／Teams |
| キュー長 | 10件を10分超える | 同上 |
| vLLM のヘルスチェック | 3回続けて失敗 | 同上（自動再起動の後も続く場合） |
| ディスク使用率 | `/srv` が 85% を超える | 同上 |
| エラー率 | 5xx が 5% を超える（5分間） | 同上 |

### 8.2 バックアップ（OPS-04）
毎晩 2:00 に実行する（`/srv/llm/deploy/backup.sh` と systemd timer）。

| 対象 | 方法 | 保持 |
| --- | --- | --- |
| PostgreSQL | `pg_dump -Fc`（コンテナ内で実行） | 日次14世代、月次12世代 |
| Qdrant | スナップショット API | 日次7世代 |
| モデル台帳・LoRA | `rsync` で差分 | 全版 |
| 設定・compose・secrets | `tar` → `age` で暗号化 | 日次30世代 |

```bash
# NAS をマウントする（資格情報ファイルは chmod 600）
sudo mkdir -p /mnt/nas-backup
echo "//nas01/llm-backup /mnt/nas-backup cifs credentials=/srv/llm/secrets/nas.cred,uid=llmadmin,gid=llmadmin,vers=3.0,_netdev,nofail 0 0" | sudo tee -a /etc/fstab
sudo mount -a
```

- 月に1回、予備機に復元する訓練を行い、手順を `projects/docs/local_llm_ops/` に追記する。

### 8.3 systemd での自動起動

```bash
sudo tee /etc/systemd/system/llm-stack.service >/dev/null <<'EOF'
[Unit]
Description=Local LLM stack (docker compose)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/srv/llm/deploy
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now llm-stack
```

- 各コンテナには `restart: unless-stopped` とヘルスチェックを付ける。Gateway は vLLM の死活を監視し、異常が続く場合は `docker restart` を実行する（OPS-02。Docker ソケットは使わず、専用の小さな監視サービスから行う）。
- UPS: `nut` で UPS を監視し、バッテリー残量が少なくなったら `systemctl stop llm-stack` → シャットダウンする。

### 8.4 障害時の再起動確認（構築完了の条件）

| # | 試験 | 期待する結果 |
| --- | --- | --- |
| 1 | `docker kill llm-vllm-12b-1` | 自動で再起動し、ヘルスチェックが通る。Gateway のキューは失われず、再投入される（RUN-09） |
| 2 | `docker compose restart gateway` | クライアントが SSE を再接続して受け取り直す |
| 3 | `sudo reboot` | OS が起動したらスタックが全部立ち上がる（5分以内が目安） |
| 4 | 電源ケーブルを抜いて挿す（UPS なしで1回だけ） | BIOS の設定で自動で電源が入り、復旧する。DB が壊れていない |
| 5 | LAN ケーブルを抜いて挿す | 復旧後にクライアントが自動で再接続する |
| 6 | web-relay 以外のコンテナから `curl https://example.com` | 失敗する（NET-12） |

---

## 付録A: Windows 11 + WSL2 で構築する場合の差分

| 項目 | Ubuntu 直接との違い・手順 |
| --- | --- |
| ドライバ | **Windows 側にだけ** NVIDIA ドライバ（RTX 50 対応版）を入れる。WSL 内に Linux 用ドライバを入れてはいけない。WSL 内では `nvidia-smi` が `/usr/lib/wsl/lib/` から使える |
| WSL の準備 | 管理者 PowerShell: `wsl --install -d Ubuntu-24.04` → `wsl --update`。WSL 内では §2.4〜§5 を同じ手順で行う（§3.1 のドライバは除く） |
| リソース | `%UserProfile%\.wslconfig` で `memory=80GB`、`processors=16`、`swap=16GB` などを指定する（既定ではホストのメモリの半分しか使えない） |
| ネットワーク | `.wslconfig` に `networkingMode=mirrored` を指定すると、Windows のIPでそのまま待ち受けられる（Windows 11 22H2 以降）。使えない場合は `netsh interface portproxy add v4tov4 listenport=443 listenaddress=0.0.0.0 connectport=443 connectaddress=<WSLのIP>` で転送する（WSL の IP は再起動で変わるので起動スクリプトで更新する） |
| ファイアウォール | ufw ではなく **Windows Defender ファイアウォール**で 443 / 22 / 3000 を社内LANに限定する。mirrored モードでは Hyper-V ファイアウォールの設定（`Set-NetFirewallHyperVVMSetting`）も確認する |
| 外向き通信の制限 | WSL 内の `DOCKER-USER`（§5.3）は同じように使える。加えて Windows 側でも不要な外向き通信を制限する |
| Docker | Docker Desktop ではなく **WSL 内に Docker Engine を直接入れる**ことを推奨する（ライセンスと自動起動の扱いが簡単になる）。`/etc/wsl.conf` に `[boot] systemd=true` を指定する |
| 自動起動 | WSL は誰もログオンしていないと起動しない。タスクスケジューラで「起動時に `wsl.exe -d Ubuntu-24.04 -u root -- systemctl start llm-stack`」を、ユーザーのログオン状態に関係なく実行するよう登録する。Windows の自動サインインは使わない |
| スリープ | Windows の電源設定でスリープ・休止を無効にし、Windows Update のアクティブ時間を業務時間に合わせる（再起動は夜間に限る） |
| ファイルシステム | モデル・DB・索引は**WSL 内の ext4（`/srv/llm`）**に置く。`/mnt/c` 上に置くと I/O が大幅に遅くなる。仮想ディスク（ext4.vhdx）は大きくなるので、置き場所を D: などのデータ用 SSD に移す（`wsl --manage <distro> --move`） |
| 性能 | GPU 推論の性能は Ubuntu 直接とほぼ同じ見込みだが、**M0 で両方を実測して比べ**、VERSIONS.md に記録する |
| バックアップ | NAS へは WSL 内から `cifs` でマウントするか、Windows 側から `\\wsl$\Ubuntu-24.04\srv\llm\backups` を robocopy する |

---

## 付録B: トラブルシュート

| 症状 | 主な原因 | 対処 |
| --- | --- | --- |
| `nvidia-smi` が「No devices were found」 | オープン版ではないカーネルモジュールを入れた／Secure Boot で読み込めない | `nvidia-driver-<ver>-open` を入れ直す。`mokutil --sb-state` を確認し、MOK を登録するか Secure Boot を無効にする |
| コンテナから GPU が見えない | Container Toolkit が未設定 | `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| vLLM の起動時に CUDA / カーネルのエラー（sm_120 未対応など） | イメージの CUDA / PyTorch が Blackwell に未対応 | Blackwell に対応した vLLM の版に上げる。対応した版を VERSIONS.md に記録する |
| vLLM が OOM で起動しない | `max-model-len` と `max-num-seqs` が VRAM に対して大きい | `--max-model-len` を下げる、`--gpu-memory-utilization` を調整する、`--kv-cache-dtype fp8` を確認する |
| guided_json が効かない（JSON が崩れる） | 版によって指定方法が違う／バックエンドが無効 | §7.2 の書き方を版に合わせる。起動ログで構造化出力のバックエンド名を確認する |
| 起動が非常に遅い | 毎回コンパイル（CUDA graph など）している | コンパイル結果のキャッシュ用ディレクトリをボリュームに永続化する。ヘルスチェックの `start_period` を延ばす |
| GPU 温度が高く、クロックが下がる | 筐体の排熱不足 | 設置場所と吸排気を見直す。`nvidia-smi -pl <W>` で電力上限を下げる（性能への影響を VERSIONS.md に記録する） |
| クライアントで証明書エラー | CA が未登録、SAN の不一致 | `ca.crt` を信頼されたルートに登録する。ホスト名・IP が SAN に入っているか確認する |
| web-relay が外に出られない | プロキシ未設定、DOCKER-USER の誤り | `web-relay.env` に `HTTPS_PROXY` を設定する。`iptables -L DOCKER-USER -n -v` で確認する |
| web-relay 以外が外に出られてしまう | frontend のサブネットの DROP ルールが抜けている | §5.3 のルールを入れ直して `netfilter-persistent save` する。§8.4 の試験6をやり直す |
| 再起動後にスタックが上がらない | NAS のマウント待ちで止まっている、`llm-stack` が無効 | fstab に `nofail` があるか確認する。`systemctl status llm-stack` と `journalctl -u llm-stack` を見る |
| WSL2: 外から接続できない | mirrored 未設定、portproxy の IP が古い | `.wslconfig` を見直して `wsl --shutdown`、または portproxy を更新する |
