# 再開

環境を失った場合は `bash scripts/bootstrap.sh --with-qwen` で固定版の依存関係・モデルを復元します。cacheのSHAが一致すれば再ダウンロードしません。

privateな作業先を `JOB=.state/job` とし、同じ入力・出力先で次を再実行します。

```bash
.venv/bin/python scripts/transcribe.py "$JOB/source.m4a" --output-dir "$JOB/asr"
.venv/bin/python scripts/recheck.py --job-dir "$JOB"
.venv/bin/python scripts/assemble_review.py --job-dir "$JOB"
```

追加認識は対象queue、組立は全区間の校正判断が必要です。入力・資料・設定・ID・校正データはJOB内にだけ保存します。各工程の準備と保存後の再取得検証は [README.md](README.md) を参照してください。原音声を変更せず、確認済みcheckpointを再利用します。
