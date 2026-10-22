import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SCREENSHOT_DIR = PROJECT_ROOT / "screenshot"
DATA_PATH = DATA_DIR / "eroterest-page.json"

BASE_URL = "https://movie.eroterest.net/"
DEFAULT_WORD = "熟女"
DEFAULT_USER_AGENT = (
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
LIST_CONTAINER_SELECTOR = ".listWrapper"
LIST_LINK_SELECTOR = '.listWrapper a[href*="/page/"]'


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="eroterest の作品一覧から詳細ページURLを収集する"
  )
  parser.add_argument("--word", default=DEFAULT_WORD, help="検索ワード")
  parser.add_argument("--page", type=int, default=1, help="一覧ページ番号")
  parser.add_argument("--headed", action="store_true", help="ブラウザを表示する")
  parser.add_argument("--timeout", type=int, default=30000, help="タイムアウト(ms)")
  parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent")
  return parser.parse_args()


def build_list_url(word: str, page: int) -> str:
  return f"{BASE_URL}?word={quote(word)}&c=&page={page}"


def load_existing(path: Path) -> dict[str, bool]:
  """既存データを {url: scraped} で読み込む。

  読めない場合は既存ファイルを退避してから空として扱い、
  壊れた既存データを新規URLで上書き流失させることを防ぐ。
  """
  if not path.exists():
    return {}

  try:
    with path.open("r", encoding="utf-8") as f:
      data = json.load(f)
  except (json.JSONDecodeError, OSError) as e:
    backup = path.with_suffix(path.suffix + ".corrupt.bak")
    path.replace(backup)
    print(f"Warning: 既存データ {path} を読み込めませんでした ({e})。")
    print(f"Warning: 既存ファイルを {backup} に退避し、空の状態で続行します。")
    return {}

  if not isinstance(data, list):
    backup = path.with_suffix(path.suffix + ".corrupt.bak")
    path.replace(backup)
    print(f"Warning: 既存データの形式が不正です。{backup} に退避します。")
    return {}

  existing: dict[str, bool] = {}
  for item in data:
    if not isinstance(item, dict):
      continue
    url = item.get("url")
    if not isinstance(url, str):
      continue
    existing[url] = bool(item.get("scraped", False))
  return existing


def merge_urls(existing: dict[str, bool], urls: list[str]) -> list[dict[str, object]]:
  """既存データを保持したまま、今回取得したURLを追加する。

  - 既存URLは並びと scraped フラグを維持する（消さない）
  - 今回初めて取得したURLは scraped=False で末尾に追加する
  """
  result: list[dict[str, object]] = []
  seen: set[str] = set()

  for url, scraped in existing.items():
    seen.add(url)
    result.append({"url": url, "scraped": scraped})

  for url in urls:
    if url in seen:
      continue
    seen.add(url)
    result.append({"url": url, "scraped": False})

  return result


def write_json_atomic(path: Path, data: list[dict[str, object]]) -> None:
  """一時ファイルに書いてから os.replace で原子的に置き換える。

  途中で中断されても既存ファイルが壊れない。
  """
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
  tmp_path = Path(tmp_name)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=2, ensure_ascii=False)
      f.flush()
      os.fsync(f.fileno())
    # mkstemp は 0600 を作るため、既存ファイルと同じモードに揃える
    os.chmod(tmp_path, path.stat().st_mode & 0o777 if path.exists() else 0o664)
    os.replace(tmp_path, path)
  except BaseException:
    tmp_path.unlink(missing_ok=True)
    raise


def save_screenshot(page: Page, path: Path) -> None:
  """スクリーンショットを保存する。失敗しても元のエラーを上書きしない。"""
  try:
    page.screenshot(path=str(path), full_page=True)
  except Exception as e:  # noqa: BLE001 - 元のエラーを隠さないため意図的に広域に捕捉
    print(f"Warning: スクリーンショットを保存できませんでした ({e})")
    return
  print(f"Screenshot saved to {path}")


def main() -> int:
  args = parse_args()
  DATA_DIR.mkdir(parents=True, exist_ok=True)
  SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

  url = build_list_url(args.word, args.page)
  print(f"Target URL: {url}")

  with sync_playwright() as p:
    context = None
    page = None
    try:
      context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROJECT_ROOT / ".browser-data"),
        headless=not args.headed,
        user_agent=args.user_agent,
      )
      page = context.new_page()

      response = page.goto(
        url,
        timeout=args.timeout,
        wait_until="domcontentloaded",
      )
      status = response.status if response else None
      print(f"Status: {status if status is not None else 'No response'}")
      print(f"URL: {page.url}")

      if status is not None and status >= 400:
        print(f"Aborting: HTTP {status} が返されました。既存データは変更しません。")
        return 1

      # JS描画の完了を待ってから抽出する（0件抽出での既存データ流失を防ぐ）
      try:
        page.wait_for_selector(
          LIST_CONTAINER_SELECTOR,
          state="attached",
          timeout=args.timeout,
        )
      except PlaywrightTimeoutError:
        print(
          f"Aborting: 一覧コンテナ {LIST_CONTAINER_SELECTOR} が見つかりませんでした。"
          " サイトの構造変更の可能性があります。既存データは変更しません。"
        )
        return 1

      urls: list[str] = page.evaluate(
        """(selector) => {
          const links = Array.from(document.querySelectorAll(selector));
          return Array.from(new Set(links.map(a => a.href)));
        }""",
        LIST_LINK_SELECTOR,
      )
      print(f"Extracted URLs count: {len(urls)}")
      for u in urls[:5]:
        print(f"  {u}")

      if not urls:
        print("Aborting: URLを1件も取得できませんでした。既存データは変更しません。")
        return 1

      existing = load_existing(DATA_PATH)
      result = merge_urls(existing, urls)
      write_json_atomic(DATA_PATH, result)
      print(f"Saved {len(result)} URLs to {DATA_PATH}")

      save_screenshot(page, SCREENSHOT_DIR / "screenshot.png")
      return 0
    except PlaywrightError as e:
      print(f"Failed to open page: {e}")
      if page is not None:
        save_screenshot(page, SCREENSHOT_DIR / "error_screenshot.png")
      return 1
    finally:
      if context is not None:
        context.close()


if __name__ == "__main__":
  sys.exit(main())
