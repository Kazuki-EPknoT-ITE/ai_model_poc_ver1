# 検証機への移行手順（プロジェクトごと移す）

2026-10-16 作成。検証機（ZOTAC MAGNUS ONE ULTRA / RTX 5080 16GB、Windows 11）に、このリポジトリと開発環境をまるごと移し、以後の開発・検証（vLLM・評価・QLoRA 学習）を検証機の上で行う。

- OS は **Windows 11 のまま**（利用者の決定）。推論サーバー一式は **WSL2（Ubuntu 24.04）の中**で動かす（`projects/docs/local_llm_ops/server_setup.md` 付録A）。
- アプリ（Electron）・試験・Claude Code は Windows 側で動かす（開発 PC と同じ）。

---

## 1. 利用者が検証機の前で行うこと（管理者の PowerShell）

1. **Windows Update** を最新にする。
2. **NVIDIA ドライバ**（RTX 5080 対応の最新版）を NVIDIA の公式サイトから入れる。PowerShell で `nvidia-smi` を実行し、RTX 5080 と出ることを確かめる。
3. **電源**: 設定 → システム → 電源 で、スリープと画面を消すを「なし」にする。
4. **有線 LAN** につなぐ（5GbE ポート）。可能なら固定 IP にする。
5. 開発ツールを入れる。版は開発 PC と同じにする。

   | ツール | 版 | 入れ方の例 |
   | --- | --- | --- |
   | Git for Windows | 2.45 以降 | `winget install Git.Git` |
   | GitHub CLI | 2.83 以降 | `winget install GitHub.cli` |
   | Node.js | **20 系（20.16 以降の 20.x）**。22 には上げない（better-sqlite3 の事前ビルドが 20 まで） | `winget install OpenJS.NodeJS.LTS` は 22 になることがあるので、**nodejs.org から 20.x の MSI** を入れる |
   | pnpm | 10.33.0（`package.json` の packageManager） | `corepack enable` → `corepack prepare pnpm@10.33.0 --activate` |
   | uv | 0.10 以降 | `winget install astral-sh.uv` |
   | Python | 3.12（uv が入れる） | `uv python install 3.12` |
   | Claude Code | 最新 | 公式の手順で入れる |
   | LibreOffice | 任意（Excel の再計算・形式変換の試験に使う。無ければその試験はスキップ） | `winget install TheDocumentFoundation.LibreOffice` |

6. **GitHub にログイン**（push に使うアカウント）: `gh auth login`。アカウントは **Kazuki-EPknoT-ITE**。トークンをチャットに貼らない。
7. **WSL2**: `wsl --install -d Ubuntu-24.04` → 再起動 → Linux のユーザー名（例 `llmadmin`）とパスワードを決める（パスワードは利用者が控える）→ `wsl --update`。
8. **Hugging Face**: Gemma 4 のモデルページで利用条件に同意する。トークンは、あとで WSL の中で `huggingface-cli login` を実行して利用者が入力する（チャットに貼らない）。

## 2. リポジトリを置いて、開発環境を作る（Claude Code でも可）

```powershell
mkdir C:\work; cd C:\work
gh repo clone Kazuki-EPknoT-ITE/ai_model_poc_ver1 ai_model_ver1
cd ai_model_ver1\projects\local-llm-agent
pnpm install
uv sync --python 3.12
# Electron の取得は install script が Node 22 を要求するので、専用スクリプトで取る
uv run python apps/desktop/scripts/fetch-electron.py
pnpm check                 # 全体の試験（pytest 536件前後が成功すれば移行できている）
pnpm test:e2e:desktop      # デスクトップアプリの E2E（13件）
```

- パスは短く、OneDrive の同期対象外に置く（`C:\work\...` を推奨）。
- `.env`・鍵・トークンはリポジトリに無い。必要になったら検証機の上で作る。

## 3. 推論サーバー一式（WSL2 の中）

`projects/docs/local_llm_ops/server_setup.md` の **付録A** と §2.4〜§5 に従う。要点:

- `%UserProfile%\.wslconfig`: `memory`（搭載メモリの8割程度）、`processors`、`swap`、`networkingMode=mirrored`
- `/etc/wsl.conf` に `[boot] systemd=true`
- WSL の中に **Docker Engine**（Docker Desktop ではなく）と **NVIDIA Container Toolkit**。ドライバは Windows 側だけ（WSL に Linux 用ドライバを入れない）
- モデルは WSL 内の `/srv/llm/models`（ext4）に置く。`/mnt/c` に置かない（遅い）
- 最初の目標（M0）: vLLM で Gemma 4 12B（QAT int4）を動かし、Gateway（`server/gateway`）経由でアプリと評価ハーネスから使う。版は `projects/local-llm-agent/server/deploy/VERSIONS.md` に記録する（まだ無い。`server_setup.md` §7.4 のテンプレートから作る）

## 4. 検証の順番（POC 条件の判定材料）

1. **M0**: vLLM＋12B が動く、制約付き生成（guided_json）が効く、`pnpm --filter @lla/eval-harness try` で1件の依頼が通る
2. **ベースモデルの評価**: `pnpm eval --set tool_tasks_synth --gateway <URL> --model-id <ベース>`（ほかのセットも）
3. **学習データの生成**: `pnpm --filter @lla/datagen gen -- --count 300 --jobs 8`（プロンプトの版 1.1 で作り直す）
4. **QLoRA 学習**: `ml/training/requirements-gpu.txt` を入れて版を固定 → `lla_train prepare` → `lla_train train` → `lla_train register`
5. **学習後の評価と比較**: 同じ評価を学習後で → `pnpm eval:compare <ベース> <学習後>`（POC 条件2）
6. 性能の実測（PRF-01〜04）、量子化の比較（T-53）、26B を CPU に一部逃がした評価

開発 PC で試した Ollama（gemma4:e4b）の経路（`dev_server.py --ollama-model`）も残っているが、検証機では vLLM を本命にする。

## 5. 作業の取り決め（Claude Code の記憶の引き継ぎ）

開発 PC の Claude Code が覚えていたこと。検証機で新しく始めるときも同じに扱う。

- **確認なしで進める**: マイルストーンごとに「このまま進める／戻す」を聞かない。試験・E2E → `ref/LOG.md` の先頭に記録（ファイルへの書き込みはこれを最後に）→ `bash scripts/checkpoint.sh "<要約>"` → **main へ push** → 次へ進む。本当に利用者の判断が要るときだけ聞く。1回の応答で複数のマイルストーンを進め、手元でできる作業が残っているのに報告だけで止まらない。「一時停止」と言われたら、すぐ止めて保存していない変更を伝える。
- **push の方法**: アカウント Kazuki-EPknoT-ITE のトークンをコマンドごとに渡す（`gh auth switch` はしない）。
  ```bash
  T=$(gh auth token --user Kazuki-EPknoT-ITE); B=$(printf 'x-access-token:%s' "$T" | base64 -w0)
  git -c credential.helper= -c http.https://github.com/.extraheader="AUTHORIZATION: basic $B" push origin main
  ```
  検証機で `gh auth login` を Kazuki-EPknoT-ITE で済ませていれば、普通の `git push` でもよい。
- **秘密情報**: 実データ・鍵・トークン・`.env` をリポジトリに入れない。チェックポイントの前に差分を grep する。
- **元に戻せない操作**（履歴の書き換え・削除など）は確認してから。
- **Node は 20 のまま**。
- サブエージェントには `ref/LOG.md` の編集・チェックポイント・git をさせない。
- 環境の注意:
  - Windows の `python` は Microsoft Store の入口のことがあるので、`uv run` か `py -3.12` を使う。
  - Bash ツールのヒアドキュメントでは、バックスラッシュ（`\n` など）がつぶれることがある。バックスラッシュを含むコードは Edit／Write ツールで書く。
  - Runtime を変えたら、デスクトップの E2E の前に `apps/desktop` を作り直す（`pnpm --filter @lla/desktop e2e` が行う）。

## 6. 移らないもの・やめるもの

- 評価の結果（`eval/harness/results/`、`eval/fault-injection/results/`）、`.coverage`、生成した学習データ（`ml/datagen/out/`）: 検証機で作り直す。
- 開発 PC の Ollama のモデル: 検証機では使わない（使うなら検証機に Ollama を入れ直す）。
- 開発 PC で作った SSH の鍵（`~/.ssh/llm_sv01_ed25519`）: プロジェクトごと移すので不要。検証機の `administrators_authorized_keys` に登録していれば、その行を消す。
- 未決の判断: `.coverage` を公開済みの履歴から消すかどうか（推奨はこのまま残す）。
