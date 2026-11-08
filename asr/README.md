# 再開可能な日本語音声認識

Parakeet Japanese CTC INT8で全音声を文字起こしし、必要な区間だけQwen3-ASR-1.7Bで追加認識します。資料に基づく校正は、Codexが作成した明示的な編集指示・追加認識の判断を適用します。コードには講義情報・音声・認識結果・認証情報を含めません。

## 環境復元

Linux x86_64、Python 3.11以上、`ffmpeg`・`curl` が必要です。Qwen補助には `git`・`gcc`・`make`・`dpkg-deb` と `libgfortran5`／`libgomp1` も必要です。管理者権限なしでOpenBLASのDebianパッケージを `.runtime/openblas` に展開します。

```bash
bash scripts/bootstrap.sh --with-qwen
```

一次認識だけなら `--with-qwen` を省略できます。`.venv` に `sherpa-onnx==1.13.8`、`soundfile==0.14.0`、`numpy==2.3.5` を準備します。モデル・ライブラリは固定revisionとSHA-256で検証し、既存ファイルが一致すれば再ダウンロードしません。

- Parakeet: revision `bef18eb066808c90bd0f5df5be685767b0732de8`、モデルSHA-256 `3addd00ef5bd1742078389e540b77394e4a508bdf2f4c9ad1b4a76d93e76598e`
- Qwen: revision `7278e1e70fe206f11671096ffdd38061171dd6e5`
- Qwen C実装: commit `924694251d9e0f18e5d86bbd06aa3ab5f870002d`
- OpenBLAS: Debian `0.3.21+ds-4`、amd64 pthread版

Qwen併用時の重みだけで約5.4 GBです。`df -h .` で空き容量を確認してください。追加で依存関係・元音声・16 kHz mono WAVを置く容量が必要です。実行時は一次認識とQwenを同時ロードしません。

クラウド環境のsetupコマンドにbootstrapを登録すると再構築できます。環境のキャッシュや公開済み環境に `.runtime` が保持されれば重みを再利用します。環境が破棄された場合は再取得が必要です。GitHubへ重みを置く方式ではありません。

## 一次認識と再開

音声はprivateな作業ディレクトリに置きます。以後、`JOB` はそのディレクトリを指します。

```bash
JOB=.state/job
mkdir -p "$JOB"
.venv/bin/python scripts/transcribe.py "$JOB/source.m4a" \
  --output-dir "$JOB/asr" --source-name '音声の表示名'
```

`--source-id` は必要な場合のみ指定します。実際のファイルIDはprivateな設定に保存してください。同じコマンドで中断箇所から再開できます。音声・モデル・tokens・設定・区間・本文SHAが一致するcheckpointだけを再利用します。設定を変える場合は別の出力先を指定します。

ffmpegで16 kHz monoへ変換し、整数sample基準で連続した20秒区間を末尾まで処理します。全音声をRAMへ読み込まず、モデルはCPU 2 threadsで最大1回ロードします。例外は最大2回再試行します。空結果も3試行後は `uncertain_empty` として本文を空のまま保存し、推測した発話を追加しません。

`asr/manifest.json`、`chunk-*.txt/json`、`progress.jsonl`、`result.json`、`raw_transcript.txt` を保存します。最終原文のatomic保存とSHA計算が成功してから `local_completed` または `local_completed_with_uncertainty` になります。`uncertain_empty_indices` は再確認対象です。wall秒・最大RSSも記録します。原音声は変更・削除しません。

## 校正と追加認識

privateな `job.json` には `title`、`source_name`、`source_url`、`references`（資料名の配列）を設定します。`source_path` は省略時 `source.m4a`、指定する場合はJOB相対または絶対パスです。実際のDrive ID・URL・資料名はこのprivateファイルだけに保存します。

```bash
.venv/bin/python scripts/review_batches.py --job-dir "$JOB" show 0 5
.venv/bin/python scripts/review_batches.py --job-dir "$JOB" apply "$JOB/edits.json"
```

編集仕様は `start`／`end`、区間番号をキーにした `edits`／`uncertain` を持ちます。各編集は置換前・置換後・期待出現数です。原文SHAと期待数を検証してから `reviewed/batch-*.json` に保存します。資料にあるだけの発話を追加しないでください。

追加認識対象は `recheck_requests.json` の `indices` に記録し、対象確定後に `sealed: true` とします。一次認識をバックグラウンドで起動する場合は、そのPIDをprivateな `primary.pid` に保存してください。追加認識スクリプトは一次完了とPID終了を待ちます。

```bash
.venv/bin/python scripts/recheck.py --job-dir "$JOB"
```

追加認識結果を確認した判断はprivateな `qwen-decisions*.json` の `segments` に保存します。各判断は `index`、`corrected_text`、`uncertain_reasons` を持ちます。全区間の一次校正と要求した追加認識の判断が揃ったら組み立てます。

```bash
.venv/bin/python scripts/assemble_review.py --job-dir "$JOB"
```

組立時に公開スクリプト `review_batches.py secondary` を呼び、全区間の連続性・原文SHA・元音声SHA・UTF-8・追加認識対象の一致を確認します。タイトル・資料名・元音声表示は `job.json` から取得します。`review/` に原文TXT、校正版TXT／Markdown、要確認箇所Markdownを生成し、`local_verification.json` に各ファイルのSHAとサイズを記録します。この段階は `ready_to_upload` であり、Drive保存完了ではありません。

## Google Drive connectorでの入出力

1. Codexの接続済みGoogle Drive connectorで、指定された元音声と資料を取得します。元音声をJOB内へ保存し、取得したファイルID・元ファイル名・SHAをprivateな `job.json` に記録します。Google側の接続操作が必要な場合はconnectorの認証画面を使い、認証情報をチャットへ貼りません。
2. 原音声のSHAを確かめてから処理し、元のDrive音声は変更・削除しません。標準入力はDrive経由です。直接添付の音声がworkspaceに取得できる場合も同じローカルコマンドを使えます。
3. connectorで `review/` の4成果物を指定出力フォルダへ保存し、それぞれのファイルID・リンクをprivate記録に残します。このコード自体にはOAuthやconnector通信を実装していません。
4. 保存済み各ファイルをconnectorで再取得し、UTF-8で読めることとSHA-256・サイズが `local_verification.json` と一致することを確認します。DriveのMD5が取得できる場合もローカルMD5と比較します。アップロード応答だけで保存完了にしません。
5. 全ファイルの再取得検証が揃ってから完了とし、保存リンクを表示します。失敗時はcheckpointとローカル成果物を維持して再試行します。

モデル・`.runtime`・`.venv`・`.state`・原音声・資料・校正判断・出力・認証情報はコミットしません。公開保存対象はスクリプトと汎用ドキュメントだけです。
