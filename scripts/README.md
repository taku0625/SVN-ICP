# Oxford Spires 全シーケンス評価

## Singularity コンテナ内で実行

ホストから起動と評価をまとめて実行する場合は、次のランチャーを使用できます。

```bash
bash docker/start_singularity.sh \
  --dataset-dir "$HOME/dataset/oxford_spires_dataset/data/sequences" \
  --oxford-spires --output-dir /work/results/oxford-run01 --play-rate 0.05
```

データのパスは実際の配置に合わせて変更してください。
[自動起動手順](../docker/README.md#起動と同時にタスクを自動実行)には、事前検証・任意スクリプトの実行例も記載しています。

1シーケンスの評価例として `scripts/my_startup.sh` を用意しています。
既定では `2024-03-12-keble-college-02` を評価します。

```bash
# ホストから起動し、1シーケンスをビルド・評価。
bash docker/start_singularity.sh -- bash scripts/my_startup.sh

# 評価対象と結果保存先を変更。
bash docker/start_singularity.sh -- bash scripts/my_startup.sh \
  --sequence 2024-03-12-keble-college-03 --output-dir /work/results/keble03-run01

# GPU 推定をせず、選択したシーケンスだけ事前検証。
bash docker/start_singularity.sh --no-gpu -- bash scripts/my_startup.sh --dry-run
```

データの場所を限定する場合は、ランチャーの `--dataset-dir` にホスト側の Oxford Spires の
ルートを指定してください。出力先を省略すると `/work/results/<シーケンス名>_<日時>_<PID>` に保存します。
コンテナ内からは `bash scripts/my_startup.sh` で同じタスクを実行できます。

[Singularity の起動手順](../docker/README.md#作成した-sif-の確認と-gpu-実行)に従い、
ソースを `/workspace/SVN-ICP:ro`、`~/dataset` を `/dataset:ro`、
書き込み可能な作業領域を `/work:rw` に bind して、以下を **`Singularity>` 内で** 実行します。

```bash
cd /workspace/SVN-ICP

# /dataset を再帰探索し、シーケンス名と実際のパスを確認。
bash scripts/run_oxford_spires_singularity.sh --dataset-dir /dataset --list-sequences

# 以降のパスは実際の配置に合わせて変更。
# bag・metadata・GT を検証。ROS ノードのビルドや GPU 推定は実行しない。
bash scripts/run_oxford_spires_singularity.sh \
  --dataset-dir /dataset/oxford_spires_dataset/data/sequences --dry-run

# 全シーケンスを逐次評価。最初にノードをビルドし、以降は増分ビルド。
bash scripts/run_oxford_spires_singularity.sh \
  --dataset-dir /dataset/oxford_spires_dataset/data/sequences \
  --output-dir /work/results/oxford-run01 --play-rate 0.05

# 対象を絞る場合。出力先には新しいディレクトリを指定する。
bash scripts/run_oxford_spires_singularity.sh \
  --dataset-dir /dataset/oxford_spires_dataset/data/sequences \
  --output-dir /work/results/oxford-run02 \
  --sequence 2024-03-12-keble-college-02 --play-rate 0.05
```

データが別の場所にある場合は `--dataset-dir` を変更してください。単一シーケンス、
Oxford Spires のルート、その親ディレクトリを指定できます。
`--output-dir` 省略時は `/work/results/oxford_spires_<日時>_<PID>` に新しい結果を保存します。
ホストの `~/svnicp-work` を `/work` に bind した場合、結果はホストの
`~/svnicp-work/results/` 配下に残ります。

ランナーは `--system-site-packages` 付きの評価用 uv 環境を `/work/uv-evaluation` に作成し、
イメージ内の Python と ROS パッケージを利用します。ROS Humble では Python 3.10 を使用し、
ROS の拡張モジュールとの ABI 互換性のため評価用 NumPy は 1.26.4 に固定しています。
`docker/uv.lock` の `evaluation` グループを使用し、`test` グループとは別の環境にします。
ソースやデータへの書き込み、SIF 内部へのインストールは不要です。

ビルド成果物は `/work/svnicp_ws/{build,install,log}` に保存します。
`stein_msgs` と `svn-icp` だけをパッケージ単位で逐次ビルドし、コンパイルの並列度は
既定2です。ノード用ビルドでは `BUILD_TESTING=OFF` とし、既存テストは後述のコマンドで別途実行します。
ビルド後はこの install を読み込み、ノード実行ファイルと共有ライブラリの解決を確認してから評価します。

```bash
# データ評価をせず、ノードのビルドとインストール確認だけ実行。
bash scripts/run_oxford_spires_singularity.sh --build-only

# ビルド済みの install を再利用。
bash scripts/run_oxford_spires_singularity.sh \
  --skip-build --dataset-dir /dataset/oxford_spires_dataset/data/sequences \
  --output-dir /work/results/oxford-run03

# H100/H200 等の compute capability 9.0 向けに絞る例。
BUILD_JOBS=2 CMAKE_CUDA_ARCHITECTURES=90 \
  bash scripts/run_oxford_spires_singularity.sh --build-only
```

`SVNICP_WORKSPACE` でビルド先、`SVNICP_EVAL_ENV` で評価用仮想環境、
`SVNICP_PYTHON` でイメージ内の Python を変更できます。
`--sequence`、`--config`、`--lidar-topic`、再生速度・タイムアウト等は既存評価スクリプトに渡します。

## Docker で実行

既存の `docker/Dockerfile` と `docker/start_docker.sh` を使用します。
CUDA 対応 NVIDIA GPU、Docker、および変換済みの ROS 2 MCAP bag が必要です。
データセットは読み取り専用でマウントし、Humble 用の metadata 互換コピーはコンテナの一時領域に作成します。

```text
oxford_spires/
  2024-03-12-keble-college-02/
    rosbag_mcap/{metadata.yaml,rosbag_mcap_0.mcap}
    processed/trajectory/gt-tum.txt
  ...
```

日付で始まる、または `rosbag_mcap` を含むシーケンスディレクトリを再帰的に自動検出します。
シーケンスが1階層以上下にある親ディレクトリや、単一シーケンスを `DATASET_DIR` に指定することもできます。
シーケンスを検出した後は、その内部の大量の PCD ファイルを探索しません。同名シーケンスが複数ある場合はエラーにします。
添付されたデータ構成では13件です。
新しいシーケンスも同じ構造で追加すれば対象になります。bag の欠損は実行エラーとして記録し、他のシーケンスを続行します。
通常実行では GT の欠損・形式不備・時刻不一致で推定を止めません。ATE を計算できない場合は評価の警告として記録します。

リポジトリルートから実行してください。

```bash
# 既存イメージでも python3-numpy がない場合は再ビルドが必要です。
bash docker/build.sh

# マウント先で検出されるシーケンスと実際のパスを表示。
DATASET_DIR=/path/to/oxford_spires bash scripts/run_oxford_spires.sh --list-sequences

# メタデータ、トピック、GT、bag ファイルの存在を事前確認（ビルドと推定は実行しない）。
DATASET_DIR=/path/to/oxford_spires bash scripts/run_oxford_spires.sh --dry-run

# 全シーケンスを逐次評価。ワークスペースはコンテナ内で一度ビルド。
DATASET_DIR=/path/to/oxford_spires bash scripts/run_oxford_spires.sh

# 出力先、再生速度、トピック、対象シーケンスの指定例。
DATASET_DIR=/path/to/oxford_spires OUTPUT_DIR=/path/to/results/run02 \
  bash scripts/run_oxford_spires.sh --play-rate 0.05 --lidar-topic /lidar/points \
  --sequence 2024-03-12-keble-college-02 --sequence 2024-03-12-keble-college-03
```

通常実行でも、ビルド前にマウント先のシーケンス配置を確認します。
検出できない場合はコンテナ内で見えているエントリも表示するため、ホストの `DATASET_DIR` と照合してください。
Oxford Spires のツール用リポジトリにデータがある場合は、例えば
`DATASET_DIR=~/dataset/oxford_spires_dataset/data/sequences/` を指定できます。

デフォルト出力先は `output/oxford_spires/` です。各シーケンスのディレクトリに
`trajectory.tum`、`svnicp.log`、`playback.log`、`progress.json`、実際に使用した `parameters.yaml`、`qos.yaml` を保存します。
`summary.csv` と `summary.json` に実行状態、エラー理由、評価値を保存します。
`status: completed` は最終 LiDAR メッセージの消化完了を表し、精度の合否判定ではありません。
対応率が低くても失敗にしません。ATE を出せない場合は `evaluation_warning` と `null` の ATE を保存します。
既存の評価結果は上書きしません。再評価には新しい `OUTPUT_DIR` を指定してください。
事前確認の結果は `validation.csv` / `validation.json` に保存し、推定成功とは区別します。
シーケンスが1件でも失敗した場合、全件処理後の終了コードは1です。
Ctrl+C で中断した場合は、そのシーケンスを `status: interrupted` として保存し、
残りのシーケンスを開始せず終了コード130で終了します。ROS の終了済みコンテキストを再終了して
元のエラーや中断理由を上書きしないよう、シーケンスごとにコンテキストを分離しています。

PointCloud2 トピックが1件なら metadata から自動選択します。複数ある場合は `--lidar-topic` が必要です。
推定前に選択した LiDAR ストリームを読み、header 時刻の範囲・XYZ フィールドを確認します。
ノードの購読開始を確認してから、信頼性を reliable に指定して LiDAR トピックだけを再生します。
bag の再生エラー・ノード異常終了・処理停止によるタイムアウトは実行エラーになります。
再生終了後も、ノードが最終 LiDAR メッセージを処理し終えるまで待ちます。
ノードは処理済みの入力時刻をナノ秒の整数で `progress.json` に保存するため、
登録スキップで軌跡の末尾時刻が入力より前になっても、軌跡の品質を理由に停止しません。
`--drain-timeout` は処理済みフレーム数が増えるたびに延長される、無進捗時間の上限です。既定値は180秒です。
`--startup-timeout` と `--sequence-timeout` も秒単位で変更できます。
新しい完了通知を使うためノードの再ビルドが必要です。上記の通常実行コマンドで自動的に `colcon build` します。
これは全シーケンスの評価であり、既存ノードのバッファ設計によって全入力フレームが処理される保証はありません。
遅延・フレーム欠落が大きい場合は `--play-rate` を下げてください。

評価は GT の `gt-tum.txt` と推定軌跡を、既定で最大0.05秒の一対一の最近傍時刻対応で照合します。
並進と回転のみで軌跡を整列し（SE(3)、スケール補正なし）、並進 ATE の RMSE・平均・中央値・最大値をメートルで出力します。
対応数と推定姿勢数、対応率も保存しますが、対応率による合否判定はしません。
`--max-difference` で GT の時刻対応の許容差を変更できます。
旧 `--min-association-ratio` と `--end-tolerance` は既存コマンドとの互換用に受理するだけで、判定には使いません。
姿勢誤差や RPE はこのスクリプトの評価対象に含みません。

`svn-icp/config/oxford_spires.yaml` は既存 niwa 設定に基づく **LiDAR-only ICP のベースライン** です。
Oxford Spires 用の最適化済みパラメータではありません。
`voxel_size: 0.2` と `map_voxel_size: 0.3` は維持し、
`SVNICP_parameters.map_max_voxels: 100000` で保持するマップ voxel 数を制限しています。
上限を超えると現在位置から遠い voxel を削除し、推定は続行します。
`map_voxel_max_points: 20` の場合、保持するマップ点は最大200万点です。
この上限はプロセス全体のメモリ上限ではなく、入力バッファや一時点群には別途メモリを使います。
上限値は設定ファイルで変更でき、`0` は個数制限なしです。他の設定ファイルで省略した場合も `0` です。
`map_range` による距離制限も併用します。処理中のログにマップ voxel 数と登録に使う点数を出力します。
PCD からの変換で点ごとの時刻が保持されない場合を考慮し、deskew は無効にしています。
GT と LiDAR の時刻系・センサ原点が一致していることを確認してください。
GT が別センサの軌跡の場合は校正に基づく変換が必要です。一定の世界座標整列だけではレバーアームの差を解消できません。
別設定を使う場合は `--config /root/svnicp_ws/src/SVN-ICP/svn-icp/config/your_config.yaml` のように
コンテナから参照できるパスを指定します。IMU 融合の KF モードは校正未設定のため受け付けません。

`Node exited during playback: 247` と `svnicp.log` の `[ros2run]: Killed` は、
ノードが SIGKILL で終了したことを示します。ホストの `journalctl -k` で OOM の有無を確認してください。
旧コードの voxel ハッシュは20ビットに切り詰めており、大きなマップでハッシュ表だけが
過剰に拡張する問題がありました。ハッシュの切り詰めと、削除で無効になるイテレーターの利用を修正しています。
点群の参照範囲も、過去の点の距離の二乗ではなく、現在の有効点の距離から計算します。

ROS/GPU がない環境でも、Python に NumPy と PyYAML があれば事前確認とテストが可能です。

```bash
python3 scripts/evaluate_oxford_spires.py --dataset-dir /path/to/oxford_spires \
  --output-dir /tmp/oxford-validation --dry-run
uv run --project docker --locked --group test python -m unittest discover -s scripts/tests -v
```

テスト用 Python 環境は `uv` で構築します。上記コマンドが `docker/uv.lock` に従って
`spython`、NumPy、PyYAML を用意するため、個別のインストールや activate は不要です。
Singularity 内でソースを読み取り専用で bind する場合は、`UV_PROJECT_ENVIRONMENT` と
`UV_CACHE_DIR` を書き込み可能な領域に設定してください。
[コンテナ内の uv 実行手順](../docker/README.md#読み取り専用-bind-内で-uv-を実行する場合)を参照してください。

C++ ユーティリティのテストには `g++` を使います。`robin-map-dev` のヘッダーも利用可能な場合は、
実際の `tsl::robin_map` に120万 voxel を挿入するメモリ拡張の回帰テストも実行します。
ROS ノード全体のビルドと実データ・GPUでの動作確認は、上記の Singularity または Docker 実行で行ってください。
