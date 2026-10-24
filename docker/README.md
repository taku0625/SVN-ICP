# Dockerfile から Singularity / Apptainer の SIF を作成

`build_singularity.sh` は既存の `docker/Dockerfile` をそのまま使い、
Docker イメージのビルド → `docker image save` → Singularity / Apptainer の
`docker-archive://` 変換を実行します。コンテナレジストリへの push は不要です。
別の定義ファイルに依存パッケージを重複して記載する必要もありません。

## 同じマシンでビルドと変換

Docker デーモンへのアクセスと、`singularity`（SingularityCE）または `apptainer` が必要です。
両方ある場合は `singularity` を優先します。リポジトリルートから実行してください。

```bash
bash docker/build_singularity.sh
# 出力: images/svnicp-humble-cuda124.sif
```

Dockerfile の CUDA 12.4、LibTorch、ROS 2 Humble、GTSAM 等が SIF に引き継がれます。
**このイメージは依存環境です。SVN-ICP のソースやコンパイル済みノードは含みません。**
作成時には GPU は不要です。GPU を使用して実行するマシンには対応する NVIDIA ドライバが必要です。

既存の Docker イメージを変換する場合はビルドを省略できます。

```bash
bash docker/build_singularity.sh \
  --skip-docker-build \
  --image svnicp:humble-cuda124 \
  --runtime apptainer \
  --output /path/to/svnicp.sif
```

`--runtime` には実行ファイルの絶対パスも指定できます。
出力ファイルが既に存在する場合は停止します。再作成するときは新しい出力先を指定してください。
変換・検査が完了してから出力先に公開し、一時 archive は成功・失敗時ともに削除します。

## Docker のあるマシンと ABCI 等で工程を分ける

Docker の利用できるマシンで archive を作成します。ここでは Singularity は不要です。

```bash
bash docker/build_singularity.sh --archive-only images/svnicp-docker.tar

# 既に Docker イメージがある場合
bash docker/build_singularity.sh \
  --skip-docker-build --archive-only images/svnicp-docker-existing.tar
```

archive と、このリポジトリを Singularity / Apptainer の使えるマシンに用意し、変換します。
ここでは Docker は不要です。`docker export` のファイルではなく、上記の `docker image save`
で作成した archive を使用してください。

```bash
bash docker/build_singularity.sh \
  --from-archive /path/to/svnicp-docker.tar \
  --output /path/to/svnicp.sif
```

archive 自体が不要なら、最初のマシンで SIF まで作成して、その SIF だけを ABCI に転送しても構いません。
大きいイメージなので、ABCI 上での変換はサイトの規則に従って十分な資源を割り当てて実行してください。

## 容量と Docker ビルドオプション

CUDA / LibTorch / ROS の Docker イメージ、非圧縮 archive、変換中の rootfs、完成した SIF が必要です。
Docker の保存領域に加え、出力先と Singularity の一時領域にも十分な空き容量を用意してください。
スクリプトの一時 archive は出力先と同じファイルシステムに作成します。
変換ランタイムの一時領域・キャッシュは、必要に応じて設定できます。

```bash
# SingularityCE の場合（各ディレクトリは事前に作成）
export SINGULARITY_TMPDIR=/scratch/your-user/singularity-tmp
export SINGULARITY_CACHEDIR=/scratch/your-user/singularity-cache

# Apptainer の場合
export APPTAINER_TMPDIR=/scratch/your-user/apptainer-tmp
export APPTAINER_CACHEDIR=/scratch/your-user/apptainer-cache
```

`--` 以降の引数は `docker/build.sh` を経由して `docker build` に渡します。
既存の `docker/build.sh` にも同じ引数を直接渡せます。

```bash
bash docker/build_singularity.sh \
  --output images/svnicp-custom.sif \
  -- --build-arg GTSAM_VERSION=4.2.0
```

プロキシを使う環境では Docker と変換ランタイムの通常のプロキシ・CA 設定を使用してください。
TLS 検証を無効にせず、必要な CA を各ビルド工程に渡してください。

## 作成した SIF の確認と GPU 実行

`singularity` の部分は `apptainer` でも同様です。
`exec` は Docker の ENTRYPOINT を実行しないため、ROS 環境が必要なコマンドには
明示的に `/ros_entrypoint.sh` を指定します。

```bash
singularity inspect images/svnicp-humble-cuda124.sif
singularity exec images/svnicp-humble-cuda124.sif \
  /ros_entrypoint.sh bash -c 'ros2 pkg prefix rclcpp && nvcc --version'

# GPU を使用するシェル。ソースはホストから bind する。
singularity exec --nv \
  --bind "$PWD:/workspace/SVN-ICP:ro" \
  --pwd /workspace/SVN-ICP \
  images/svnicp-humble-cuda124.sif /ros_entrypoint.sh bash
```

SIF の内部は読み取り専用です。SVN-ICP のビルド成果物・ログ・評価結果は、
別途 bind した書き込み可能なワークスペースに保存してください。
