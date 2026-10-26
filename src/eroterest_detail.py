import argparse
import json
import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from utils import (
  DATA_PATH,
  DEFAULT_USER_AGENT,
  PROJECT_ROOT,
  SCREENSHOT_DIR,
  save_screenshot,
  write_json_atomic,
)


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="eroterest-page.json のURLを基に詳細ページをスクレイピングする"
  )
  parser.add_argument("--timeout", type=int, default=30000, help="タイムアウト(ms)")
  parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent")
  parser.add_argument(
    "--limit",
    type=int,
    default=0,
    help="一度に処理する最大件数（0以下の場合は未処理のものを全て処理）",
  )
  return parser.parse_args()


def main() -> int:
  args = parse_args()
  SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

  if not DATA_PATH.exists():
    print(f"Error: 対象のJSONファイルが見つかりません: {DATA_PATH}")
    print("先に eroterest_list.py を実行してURLリストを生成してください。")
    return 1

  try:
    with DATA_PATH.open("r", encoding="utf-8") as f:
      items = json.load(f)
  except (json.JSONDecodeError, OSError) as e:
    print(f"Error: JSONファイルを読み込めませんでした: {e}")
    return 1

  if not isinstance(items, list):
    print("Error: JSONデータの形式が不正です（リストである必要があります）。")
    return 1

  # 未処理 (scraped: false) の項目を抽出
  unprocessed_indices = [
    i
    for i, item in enumerate(items)
    if isinstance(item, dict) and not item.get("scraped", False)
  ]

  if not unprocessed_indices:
    print("処理対象のURL（scraped: false）は存在しません。")
    return 0

  print(f"未処理のURL件数: {len(unprocessed_indices)}")

  processed_count = 0
  with sync_playwright() as p:
    context = None
    page = None
    try:
      context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROJECT_ROOT / ".browser-data"),
        headless=True,
        user_agent=args.user_agent,
      )
      page = context.new_page()

      for idx in unprocessed_indices:
        if args.limit > 0 and processed_count >= args.limit:
          print(f"指定された制限件数 ({args.limit}件) に達したため終了します。")
          break

        item = items[idx]
        url = item.get("url")
        if not isinstance(url, str):
          continue

        print(f"\n--- Scraping Detail [{processed_count + 1}] ---")
        print(f"URL: {url}")

        try:
          response = page.goto(
            url,
            timeout=args.timeout,
            wait_until="domcontentloaded",
          )
          status = response.status if response else None
          print(f"Status: {status if status is not None else 'No response'}")

          if status is not None and status >= 400:
            print(f"Warning: HTTP {status} が返されました。スキップします。")
            continue

          # TODO: 詳細ページ固有のスクレイピング処理をここに実装する
          # 例: タイトルや動画リンク、サムネイル等の抽出

          # 処理成功したら scraped を True に更新
          items[idx]["scraped"] = True
          processed_count += 1

        except PlaywrightError as e:
          print(f"Error scraping {url}: {e}")
          continue

      # 更新したJSONをアトミックに書き込み
      write_json_atomic(DATA_PATH, items)
      print(f"\nSuccessfully scraped {processed_count} items. Updated {DATA_PATH}")

      if page is not None:
        save_screenshot(page, SCREENSHOT_DIR / "detail_screenshot.png")
      return 0

    except PlaywrightError as e:
      print(f"Failed to launch browser: {e}")
      return 1
    finally:
      if context is not None:
        context.close()


if __name__ == "__main__":
  sys.exit(main())
