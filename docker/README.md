# spython で Dockerfile から Singularity / Apptainer の SIF を作成

`build_singularity.sh` は **Singularity Python (`spython`)** の `DockerParser` と
`SingularityWriter` を使って既存の `docker/Dockerfile` を `.def` に変換し、
SingularityCE / Apptainer で `.sif` をビルドします。Docker は不要です。

## インストールとビルド

Python の仮想環境等で、変換用パッケージをインストールしてください。
SIF のビルドには別途 `singularity` または `apptainer` が必要です。

```bash
python3 -m pip install -r docker/requirements-singularity.txt
bash docker/build_singularity.sh --fakeroot
```

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
`SPYTHON_PYTHON=/path/to/venv/bin/python` で spython をインストールした Python を選べます。

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

## 作成した SIF の確認と GPU 実行

この SIF は CUDA 12.4、LibTorch、ROS 2 Humble、GTSAM 等の**依存環境**です。
SVN-ICP のソースやコンパイル済みノードは含みません。
ビルドに GPU は不要です。GPU 実行には対応する NVIDIA ドライバが必要です。

```bash
singularity inspect images/svnicp-humble-cuda124.sif
singularity exec images/svnicp-humble-cuda124.sif \
  /ros_entrypoint.sh bash -c 'ros2 pkg prefix rclcpp && nvcc --version'

singularity exec --nv \
  --bind "$PWD:/workspace/SVN-ICP:ro" \
  --pwd /workspace/SVN-ICP \
  images/svnicp-humble-cuda124.sif /ros_entrypoint.sh bash
```

`singularity` は `apptainer` に置き換えられます。
`exec` では ROS の初期化用に明示的に `/ros_entrypoint.sh` を指定します。
ソースは bind し、ビルド成果物・ログ・結果は書き込み可能な別ワークスペースに保存してください。
