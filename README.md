# Uploader

## Playwrightの実行方法

このプロジェクトでPlaywrightを使用するスクリプト（例: `test.py`）を実行する場合、ブラウザの格納先（`PLAYWRIGHT_BROWSERS_PATH`）として `.browsers` ディレクトリを指定して実行してください。

環境変数を `.env` ファイルから読み込んで `uv` で実行する場合は、`--env-file .env` オプションを使用します。

```bash
PLAYWRIGHT_BROWSERS_PATH=.browsers uv run --env-file .env test.py
```
