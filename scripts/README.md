# Oxford Spires 全シーケンス評価

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

日付で始まるシーケンスディレクトリをすべて自動検出します。添付されたデータ構成では13件です。
新しいシーケンスも同じ構造で追加すれば対象になります。bag や GT の欠損は失敗として記録し、他のシーケンスを続行します。

リポジトリルートから実行してください。

```bash
# 既存イメージでも python3-numpy がない場合は再ビルドが必要です。
bash docker/build.sh

# メタデータ、トピック、GT、bag ファイルの存在を事前確認（ビルドと推定は実行しない）。
DATASET_DIR=/path/to/oxford_spires bash scripts/run_oxford_spires.sh --dry-run

# 全シーケンスを逐次評価。ワークスペースはコンテナ内で一度ビルド。
DATASET_DIR=/path/to/oxford_spires bash scripts/run_oxford_spires.sh

# 出力先、再生速度、トピック、対象シーケンスの指定例。
DATASET_DIR=/path/to/oxford_spires OUTPUT_DIR=/path/to/results/run02 \
  bash scripts/run_oxford_spires.sh --play-rate 0.05 --lidar-topic /lidar/points \
  --sequence 2024-03-12-keble-college-02 --sequence 2024-03-12-keble-college-03
```

デフォルト出力先は `output/oxford_spires/` です。各シーケンスのディレクトリに
`trajectory.tum`、`svnicp.log`、`playback.log`、実際に使用した `parameters.yaml`、`qos.yaml` を保存します。
`summary.csv` と `summary.json` に成功・失敗、エラー理由、評価値を保存します。
既存の評価結果は上書きしません。再評価には新しい `OUTPUT_DIR` を指定してください。
事前確認の結果は `validation.csv` / `validation.json` に保存し、推定成功とは区別します。
シーケンスが1件でも失敗した場合、全件処理後の終了コードは1です。

PointCloud2 トピックが1件なら metadata から自動選択します。複数ある場合は `--lidar-topic` が必要です。
推定前に選択した LiDAR ストリームを読み、header 時刻の範囲・XYZ フィールドを確認します。
ノードの購読開始を確認してから、信頼性を reliable に指定して LiDAR トピックだけを再生します。
bag の再生エラー・ノード異常終了・タイムアウトは失敗になります。
再生後も最終 LiDAR 時刻付近までの出力を待ち、先頭と末尾の欠落が既定の1秒を超える軌跡は評価しません。
`--end-tolerance`、`--startup-timeout`、`--drain-timeout`、`--sequence-timeout` は秒単位で変更できます。
これは全シーケンスの評価であり、既存ノードのバッファ設計によって全入力フレームが処理される保証はありません。
遅延・フレーム欠落が大きい場合は `--play-rate` を下げてください。

評価は GT の `gt-tum.txt` と推定軌跡を、既定で最大0.05秒の一対一の最近傍時刻対応で照合します。
並進と回転のみで軌跡を整列し（SE(3)、スケール補正なし）、並進 ATE の RMSE・平均・中央値・最大値をメートルで出力します。
対応数と推定姿勢数、対応率も保存し、既定で対応率80%未満は失敗とします。
`--max-difference` と `--min-association-ratio` で閾値を変更できます。
姿勢誤差や RPE はこのスクリプトの評価対象に含みません。

`svn-icp/config/oxford_spires.yaml` は既存 niwa 設定に基づく **LiDAR-only ICP のベースライン** です。
Oxford Spires 用の最適化済みパラメータではありません。
PCD からの変換で点ごとの時刻が保持されない場合を考慮し、deskew は無効にしています。
GT と LiDAR の時刻系・センサ原点が一致していることを確認してください。
GT が別センサの軌跡の場合は校正に基づく変換が必要です。一定の世界座標整列だけではレバーアームの差を解消できません。
別設定を使う場合は `--config /root/svnicp_ws/src/SVN-ICP/svn-icp/config/your_config.yaml` のように
コンテナから参照できるパスを指定します。IMU 融合の KF モードは校正未設定のため受け付けません。

ROS/GPU がない環境でも、Python に NumPy と PyYAML があれば事前確認とテストが可能です。

```bash
python3 scripts/evaluate_oxford_spires.py --dataset-dir /path/to/oxford_spires \
  --output-dir /tmp/oxford-validation --dry-run
python3 -m unittest discover -s scripts/tests -v
```
