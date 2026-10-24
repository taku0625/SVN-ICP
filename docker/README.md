# spython で Dockerfile から Singularity / Apptainer の SIF を作成

`build_singularity.sh` は **Singularity Python (`spython`)** の `DockerParser` と
`SingularityWriter` を使って既存の `docker/Dockerfile` を `.def` に変換し、
SingularityCE / Apptainer で `.sif` をビルドします。Docker は不要です。

## インストールとビルド

`uv` と Python 3.10 以降を使用します。依存関係は `docker/pyproject.toml` と
`docker/uv.lock` で管理し、仮想環境は `docker/.venv` に作成します。
SIF のビルドには別途 `singularity` または `apptainer` が必要です。

```bash
uv sync --project docker --locked
bash docker/build_singularity.sh --fakeroot
```

ビルドスクリプトも内部で `uv run --locked` を実行するため、仮想環境の activate は不要です。
上の `uv sync` を省略してスクリプトから環境を作成することもできます。
依存関係の取得が済んでいれば、定義ファイルの変換にはネットワーク接続は不要です。

既定の生成先は次の2ファイルです。

- `images/svnicp-humble-cuda124.def`: spython で変換した定義ファイル
- `images/svnicp-humble-cuda124.sif`: 完成したイメージ

`--fakeroot` はサイトで許可・設定されている場合に使用してください。
管理者権限でビルドする場合等、不要な環境では省略できます。
ビルド権限がない環境では、以下の定義ファイル生成まで実行できます。

## 定義ファイルだけ生成する

Singularity / Apptainer や管理者権限がなくても、spython による変換はできます。

```bash
bash docker/build_singularity.sh --definition-only

# 保存先を指定
bash docker/build_singularity.sh --definition-only --definition /path/to/Singularity.def
```

生成した `.def` をビルド可能なマシンに転送し、実行します。

```bash
singularity build --fakeroot svnicp.sif Singularity.def
# または
apptainer build --fakeroot svnicp.sif Singularity.def
```

以前の Docker archive 方式の `--archive-only`、`--from-archive`、`--skip-docker-build`、
`--image` は使用しません。既存の Dockerfile は変更せず、spython の変換を補正して
`SHELL`、`ARG` の引用・export、`ENV` の変数展開、各 `RUN` の終了状態と実行境界を維持します。
変換器はこのリポジトリの単一ステージ Dockerfile を対象とし、複数ステージはエラーにします。

## 出力先・ビルド引数の指定

```bash
bash docker/build_singularity.sh \
  --runtime apptainer \
  --fakeroot \
  --output /path/to/svnicp.sif \
  --definition /path/to/svnicp.def \
  --build-arg GTSAM_VERSION=4.2.0 \
  --build-arg 'CMAKE_CUDA_ARCHITECTURES=80;90'
```

`--build-arg NAME=VALUE` は繰り返せます。Dockerfile にない引数はエラーになります。
`--dockerfile FILE` で入力を変更できます。`--` 以降は Singularity / Apptainer の
`build` オプションに渡します（Docker のビルドオプションではありません）。
`--runtime` には実行ファイルの絶対パスも指定できます。
省略時は `singularity`、次いで `apptainer` を探します。
通常は uv が管理する Python を使用します。
既存の環境を明示的に使う場合だけ、`SPYTHON_PYTHON=/path/to/venv/bin/python` で上書きできます。

既存の定義ファイル・SIF は上書きしません。再作成する場合は新しい出力先を指定してください。
SIF のビルドに失敗した場合も `.def` は確認・再試行用に残します。
ビルド・検査が成功した SIF だけを出力先に公開します。

## 容量とネットワーク

CUDA / LibTorch / ROS を含む非圧縮 rootfs、ダウンロードキャッシュ、完成した SIF の
保存領域が必要です。必要に応じて一時領域とキャッシュを設定してください。

```bash
# SingularityCE（ディレクトリは事前に作成）
export SINGULARITY_TMPDIR=/scratch/your-user/singularity-tmp
export SINGULARITY_CACHEDIR=/scratch/your-user/singularity-cache

# Apptainer
export APPTAINER_TMPDIR=/scratch/your-user/apptainer-tmp
export APPTAINER_CACHEDIR=/scratch/your-user/apptainer-cache
```

ベースイメージの取得と `%post` 内の apt・Git・LibTorch の取得にネットワーク接続が必要です。
プロキシ環境ではランタイムとビルド内の各ツールに対応する CA 設定を使用し、TLS 検証を維持してください。
ABCI ではサイトの規則に従ってビルド資源を割り当てるか、別マシンで作成した SIF を転送してください。

## 起動と同時にタスクを自動実行

**ホスト側**で `start_singularity.sh` を実行すると、ソース・データ・作業領域を bind し、
ROS 環境を読み込んでから指定タスクを開始します。引数を省略した場合は
Oxford Spires のビルドと全シーケンスの逐次評価を自動実行します。
既存の SIF と、ホスト上の最新のスクリプトを使用できます。

```bash
# images/svnicp-humble-cuda124.sif を起動して評価を開始。
# ~/dataset -> /dataset:ro、~/svnicp-work -> /work:rw を自動設定。
bash docker/start_singularity.sh

# データの場所と評価オプションを指定。
bash docker/start_singularity.sh \
  --dataset-dir "$HOME/dataset/oxford_spires_dataset/data/sequences" \
  --oxford-spires --output-dir /work/results/oxford-run01 --play-rate 0.05

# GPU を使わず、起動直後にシーケンス一覧を表示して終了。
bash docker/start_singularity.sh --no-gpu --oxford-spires --list-sequences

# 起動直後に別のコマンドを実行。
bash docker/start_singularity.sh -- \
  bash -c 'ros2 pkg prefix rclcpp && nvcc --version'

# 用意済みの起動タスクで1シーケンスを評価（既定: keble-college-02）。
bash docker/start_singularity.sh -- bash scripts/my_startup.sh

# 別のシーケンスを指定。
bash docker/start_singularity.sh -- bash scripts/my_startup.sh \
  --sequence 2024-03-12-keble-college-03 --output-dir /work/results/keble03-run01

# 対話シェルを開く。
bash docker/start_singularity.sh --shell
```

`--image FILE`、`--work-dir DIR`、`--runtime apptainer` でイメージ・作業領域・ランタイムを変更できます。
`--dataset-dir` は **ホスト側**のパスです。`--oxford-spires` より後ろは評価ランナーへ渡すため、
出力先や設定ファイルには `/work`、`/dataset`、`/workspace/SVN-ICP` 配下の **コンテナ内**のパスを使います。
ランチャー用のオプションは `--oxford-spires` または `--` より前に指定してください。

`SIF_IMAGE`、`SVNICP_DATASET_DIR`、`SVNICP_WORK_DIR` でもホスト側の既定値を指定できます。
`BUILD_JOBS`、`CMAKE_CUDA_ARCHITECTURES`、`ROS_DOMAIN_ID` を設定している場合はコンテナへ渡します。
GPU の割り当てはスケジューラの環境を継承します。
作業ディレクトリが作成できない場合等は起動前に停止し、タスクの終了コードはランチャーの終了コードになります。
結果やビルド成果物はホスト側の作業領域に残ります。
`my_startup.sh` は既定で `2024-03-12-keble-college-02` の1件だけを評価し、
`/work/results/<シーケンス名>_<日時>_<PID>` に保存します。
`--dry-run` や `--skip-build` 等も指定できます。

### uv を同梱した SIF へ再ビルドする

Dockerfile は `uv==0.12.23` を導入し、ビルド中に `/usr/local/bin/uv --version` を確認します。
以前作成した SIF を更新する場合は、新しい出力先を指定して再ビルドしてください。

```bash
git pull --ff-only
bash docker/build_singularity.sh --fakeroot \
  --output images/svnicp-humble-cuda124-uv.sif

singularity exec images/svnicp-humble-cuda124-uv.sif /usr/local/bin/uv --version

bash docker/start_singularity.sh \
  --image images/svnicp-humble-cuda124-uv.sif \
  -- bash scripts/my_startup.sh
```

`--output` で指定した SIF と、同名の `.def` が既にある場合は別の出力先を指定してください。

### 既存 SIF を使う場合の uv 自動準備

既存 SIF で評価を実行する場合も、ランナーが `uv` を自動的に探します。
見つからなければ公式の portable バイナリを SHA-256 検証後に
`/work/tools/uv-0.12.23/uv` へ導入して再利用します。ホスト側では通常
`~/svnicp-work/tools/uv-0.12.23/uv` に保存されます。

```bash
# ホストで更新後、同じ起動コマンドで実行。
git pull --ff-only
bash docker/start_singularity.sh -- bash scripts/my_startup.sh

# 計算ノードでの初回ダウンロードを避ける場合は、ホストで事前に準備できます。
SVNICP_UV_DIR="$HOME/svnicp-work/tools/uv-0.12.23" \
  bash scripts/ensure_uv.sh --install
```

`SVNICP_UV=/path/to/uv` で利用可能な実行ファイルを指定できます。
自動導入先は `SVNICP_UV_DIR` で変更できます。ホストで事前準備する場合は、
`--work-dir` で指定する作業領域の `tools/uv-0.12.23` に保存してください。

## 作成した SIF の確認と GPU 実行

この SIF は CUDA 12.4、LibTorch、ROS 2 Humble、GTSAM 等の**依存環境**です。
SVN-ICP のソースやコンパイル済みノードは含みません。
ビルドに GPU は不要です。GPU 実行には対応する NVIDIA ドライバが必要です。
以下はホスト側のリポジトリルートで実行します。コンテナ内の場合は先に `exit` で戻ってください。

```bash
singularity inspect images/svnicp-humble-cuda124.sif
singularity exec images/svnicp-humble-cuda124.sif \
  /ros_entrypoint.sh bash -c 'ros2 pkg prefix rclcpp && nvcc --version'

# uv の仮想環境・キャッシュ・ビルド成果物を置く、ホスト側の作業領域。
# 以前の SVNICP_WORK_DIR の値も、この代入で更新する。
SVNICP_WORK_DIR="$HOME/svnicp-work"
mkdir -p -- "$SVNICP_WORK_DIR" &&
singularity exec --nv \
  --bind "$PWD:/workspace/SVN-ICP:ro" \
  --bind "$HOME/dataset:/dataset:ro" \
  --bind "$SVNICP_WORK_DIR:/work:rw" \
  --env UV_PROJECT_ENVIRONMENT=/work/uv-env \
  --env UV_CACHE_DIR=/work/uv-cache \
  --env UV_PYTHON=/usr/bin/python3 \
  --pwd /workspace/SVN-ICP \
  images/svnicp-humble-cuda124.sif /ros_entrypoint.sh bash
```

作業領域は自身のホーム配下に作成します。別の保存先を使う場合は、
`SVNICP_WORK_DIR` の代入を、利用者が書き込める絶対パスに変更してください。
`mkdir` と起動を `&&` でつなぎ、ディレクトリ作成に失敗した場合はコンテナを起動しません。
ホストの `~/dataset` はコンテナ内の `/dataset` から読み取り専用で参照できます。
コンテナ内で `ls /dataset` を実行して配置を確認し、評価スクリプトの `--dataset-dir` には
`/dataset` 配下の実際のシーケンスディレクトリを指定してください。評価結果は `/work` 配下に保存します。

`singularity` は `apptainer` に置き換えられます。
`exec` では ROS の初期化用に明示的に `/ros_entrypoint.sh` を指定します。
ソースは bind し、ビルド成果物・ログ・結果は書き込み可能な別ワークスペースに保存してください。

### コンテナ内で Oxford Spires を評価する

上のシェル内から次のランナーを使用します。

```bash
bash scripts/run_oxford_spires_singularity.sh --dataset-dir /dataset --list-sequences
bash scripts/run_oxford_spires_singularity.sh \
  --dataset-dir /dataset/oxford_spires_dataset/data/sequences \
  --output-dir /work/results/oxford-run01
```

`--dataset-dir` は実際のシーケンス配置に合わせて変更してください。
ランナーが `/work` 内に評価用 uv 環境とビルド先を用意し、ノードをビルドして評価します。
ROS 用 Python と NumPy 1 系を使うため、既存のテスト用 uv 環境とは分けて管理します。
詳細と `--dry-run` / `--build-only` / `--skip-build` の使用例は
[評価手順](../scripts/README.md#singularity-コンテナ内で実行)を参照してください。

### 読み取り専用 bind 内で uv を実行する場合

`uv` は既定で `docker/.venv` に依存関係をインストールします。
上記の `:ro` bind では書き込めないため、`UV_PROJECT_ENVIRONMENT` を
書き込み可能な作業領域へ変更します。`UV_CACHE_DIR` も同じ領域に指定します。
上の起動例では、この2つを `/work` 配下に設定済みです。
ROS 2 Humble の Python パッケージを使えるよう、コンテナ内の `/usr/bin/python3`
（Ubuntu 22.04 の Python 3.10）を `UV_PYTHON` で選択しています。
コンテナ内で作成する仮想環境は、ホスト側とは別の作業領域で管理してください。

既に `Singularity>` のシェルを開いている場合は、一時領域を使って実行できます。

```bash
SVNICP_UV_DIR="$(mktemp -d /tmp/svnicp-uv.XXXXXX)"
export UV_PROJECT_ENVIRONMENT="$SVNICP_UV_DIR/venv"
export UV_CACHE_DIR="$SVNICP_UV_DIR/cache"

uv run --python /usr/bin/python3 --project docker --locked --group test \
  python -m unittest discover -s scripts/tests -v
```

この一時領域はセッションごとに作成します。環境を再利用する場合は、上の `/work`
bind のようにホストの書き込み可能なディレクトリに保存してください。

## テスト

```bash
uv run --project docker --locked --group test \
  python -m unittest discover -s scripts/tests -v
```

`test` グループで評価スクリプト用の NumPy と PyYAML も導入します。
C++ ユーティリティのテストにはホストの `g++` と、追加のメモリ回帰テストには robin-map のヘッダーが必要です。
コンテナ内でソースを `:ro` bind している場合は、前節の uv 環境変数を設定してから実行してください。
