# ABCI: 1シーケンス1 GPUで全シーケンスを評価

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
