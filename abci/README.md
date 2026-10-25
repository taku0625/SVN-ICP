# ABCI: 1シーケンス1 GPUで全シーケンスを評価

## RAL 2026 の速度別 rosbag

`~/dataset/rosbag/ral2026` の次の構成には `submit_ral2026.sh` を使用してください。

```text
ral2026/
  speed1/ ... speed5/
    keble-college-02/ ...
      bag/metadata.yaml
      bag/bag_0.mcap
      ground-truth-lidar-tum.txt
      ground-truth-tum.txt
```

添付のツリーは5速度 × 13シーケンスの65 rosbagです。
全件を自動検出し、ビルド1件＋評価65件を投入します。各評価は1 GPUを要求し、
ビルド成功後、空きGPU枠に応じて並列実行されます。SIFの再ビルドは不要です。

ABCI のホスト側で、リポジトリのルートから実行してください。
`FRAI` と `FRAI_RES` に設定済みの値を export するか、`--project "$FRAI" --queue "$FRAI_RES"` で渡します。

```bash
export FRAI FRAI_RES

# 任意: 投入せずに65件のジョブを生成・確認する。
bash abci/submit_ral2026.sh \
  --run-dir "$HOME/svnicp-work/abci/ral2026-plan01" --plan

# 全件を投入する。--run-dir は毎回未使用のパスを指定。
bash abci/submit_ral2026.sh \
  --run-dir "$HOME/svnicp-work/abci/ral2026-run01"

# 完了後、速度別の結果を summary.json / summary.csv に集約する。
bash abci/submit_ral2026.sh collect \
  --run-dir "$HOME/svnicp-work/abci/ral2026-run01"
```

データの既定パスは `$HOME/dataset/rosbag/ral2026`、SIFは
`images/svnicp-humble-cuda124-uv.sif` です。`--dataset-dir` / `--image` で変更できます。
個別に試す場合は `--sequence speed1/keble-college-02` を指定します（繰り返し指定可能）。
`--walltime`、`--play-rate`、`--lidar-topic`、`--skip-build --work-dir ...` も以下と共通です。

`bag/` の metadata から PointCloud2 トピックを検出し、
**`ground-truth-lidar-tum.txt`** に対してSE(3)整列したATEを計算します。
投入前に全件の metadata・bagファイル・LiDAR GT を検証します。LiDAR GT が欠けている場合は停止します。
`ground-truth-tum.txt` を使う必要がある場合のみ `--gt-file ground-truth-tum.txt` を明示し、
その座標系が推定軌跡と対応することを確認してください。
`simulation.json` や `retiming.csv` による追加変換は行わず、bagとGTに保存された時刻を使用します。
速度条件を変える `speedN` と再生速度 `--play-rate` は別の設定です。再生速度の既定値は全件共通で `0.05` です。

結果の `sequence` は `speed1/keble-college-02` のような識別子、`speed` は `speed1` 等です。
`jobs/speed1/keble-college-02/{stdout,stderr}.log` のようにログを分け、
結果保存先は `manifest.json` の各 `results_dir` に記録します。その直下に `summary.json`、
さらに `<speed>/<sequence>/` 配下に `trajectory.tum` 等が作成されます。
ROSドメインの既定値は0から連番で、65件では0〜64を使用します。

## Oxford Spires の元データ

`submit_all.sh` はホスト側でシーケンスを検出し、次の資源指定で `abci/job.sh` を投入します。
`FRAI` と `FRAI_RES` には利用プロジェクト・キューを設定してください。

```bash
qsub -P "$FRAI" -q "$FRAI_RES" -l select=1 -l walltime=1:00:00 -v RTYPE=rt_HG abci/job.sh
```

最初にビルド用の1ジョブを投入します。続いて各シーケンスの1 GPUジョブをすべて投入し、
PBS の `depend=afterok:<ビルドジョブID>` でビルド成功後に実行を開始させます。
13シーケンスなら、ビルド1件＋評価13件です。
評価ジョブ同士には依存関係を付けないため、空きGPUとプロジェクトの利用枠に応じて並列実行されます。
全件の同時開始には、シーケンス数分のGPU枠が必要です。

## 投入

既存の `images/svnicp-humble-cuda124-uv.sif` を使用します。
ソース、SIF、データ、作業領域、投入先ディレクトリは計算ノードから同じ絶対パスで
見える共有ストレージに置いてください。投入後は実行完了までソースとSIFを維持してください。

```bash
cd /path/to/SVN-ICP
export FRAI FRAI_RES

# ファイルと投入コマンドを生成するだけ。qsub は呼び出さない。
bash abci/submit_all.sh \
  --dataset-dir "$HOME/dataset/oxford_spires_dataset/data/sequences" \
  --run-dir "$HOME/svnicp-work/abci/oxford-plan01" --plan

# 全シーケンスを投入。--run-dir には未使用のパスを指定。
bash abci/submit_all.sh \
  --dataset-dir "$HOME/dataset/oxford_spires_dataset/data/sequences" \
  --run-dir "$HOME/svnicp-work/abci/oxford-run01"
```

`--dataset-dir` は実際の配置に合わせて変更してください。`~/dataset` 全体や、
単一シーケンスも指定できます。`--sequence NAME` を繰り返すと対象を限定できます。
`FRAI` / `FRAI_RES` を export しない場合は `--project "$FRAI" --queue "$FRAI_RES"` で渡せます。

`--image FILE` で SIF、`--play-rate` で再生速度、`--lidar-topic` で LiDAR トピックを指定できます。
`--build-jobs` はコンパイル並列度（既定2）、`--cuda-architectures 90` は H100/H200 向けの例です。
既定の制限時間はビルド・評価ともに `1:00:00` です。必要に応じて `--walltime H:MM:SS` で変更してください。
再生速度の既定値 `0.05` では、bag の収録時間の約20倍の再生時間に処理時間が加わるため、
データの長さに合わせて制限時間を設定してください。

既に `/work` として使っていた作業領域にビルド済みの install があれば、
次のようにビルドジョブを省略できます。

```bash
bash abci/submit_all.sh \
  --dataset-dir "$HOME/dataset/oxford_spires_dataset/data/sequences" \
  --run-dir "$HOME/svnicp-work/abci/oxford-run02" \
  --work-dir "$HOME/svnicp-work" --skip-build
```

## ビルド・結果・ログ

既定の作業領域は `<run-dir>/work` です。各コンテナに同じ場所を `/work` として bind し、
`/work/svnicp_ws/install` と評価用 uv 環境を共有します。
ビルド完了後の評価ジョブは `--skip-build` で起動するため、並列ビルドの競合を避けられます。
uv キャッシュ・仮想環境は同じ依存関係で再利用します。

`manifest.json` に各ジョブID、投入応答、結果保存先、実際の qsub コマンドを記録します。
`prepare/{stdout,stderr}.log` にビルドのログ、`jobs/<sequence>/{stdout,stderr}.log` に評価ログが残ります。
評価結果は作業領域の `results/oxford_batch_<ID>/<sequence>/` に保存します。
その直下に `summary.json`、さらに `<sequence>/` 配下に `trajectory.tum` 等が作成されます。

ジョブごとに異なる `ROS_DOMAIN_ID` と `ROS_LOG_DIR` を指定します。
同じノードで別のバッチと併走させる場合は、重複しないよう `--domain-base` を変更してください。
GPU番号はスクリプト側で固定せず、スケジューラの割り当てを継承します。

## 集約とジョブ管理

```bash
qstat -u "$USER"

bash abci/submit_all.sh collect \
  --run-dir "$HOME/svnicp-work/abci/oxford-run01"
```

`collect` は `<run-dir>/summary.json` と `summary.csv` に全件を集約します。
全件完了は終了コード0、失敗・ビルド失敗による待機は1、未完了ありは2です。
投入成功だけでは評価成功と判定せず、ジョブ終了状態とシーケンスの結果ファイルを確認します。

ビルドの投入に失敗した場合は評価ジョブを投入しません。
個別評価の投入失敗は記録し、残りのシーケンスの投入を続けます。
ビルド自体が実行中に失敗した場合、依存ジョブは開始されないため `prepare` のログと `qstat` を確認してください。
PBS による強制終了では状態ファイルが更新されないことがあるため、残った pending/running 状態は
`qstat` とログで確認してください。キャンセルは各ジョブIDに対して `qdel` を使用します。

投入済みジョブは、投入側スクリプトを中断しても継続します。
既存の実行ディレクトリや開始済みタスクを再利用せず、再実行には新しい `--run-dir` を指定してください。
`abci/job.sh` の直接投入も、従来どおり既定の1シーケンス評価に使用できます。
