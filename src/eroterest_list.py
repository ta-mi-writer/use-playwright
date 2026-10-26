import argparse
import re
import sys
from urllib.parse import quote

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from utils import (
  DATA_DIR,
  DATA_PATH,
  DEFAULT_USER_AGENT,
  PROJECT_ROOT,
  SCREENSHOT_DIR,
  load_existing,
  merge_urls,
  save_screenshot,
  write_json_atomic,
)

BASE_URL = "https://movie.eroterest.net/"
DEFAULT_WORD = "熟女"
LIST_CONTAINER_SELECTOR = ".listWrapper"
LIST_LINK_SELECTOR = '.listWrapper a[href*="/page/"]'


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="eroterest の作品一覧から詳細ページURLを収集する"
  )
  parser.add_argument("--word", default=DEFAULT_WORD, help="検索ワード")
  parser.add_argument("--timeout", type=int, default=30000, help="タイムアウト(ms)")
  parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent")
  parser.add_argument(
    "--within-hours",
    type=int,
    default=1,
    help="1時間以内でスクレイピングする時間（時間）。これを超える時間のカードは対象外",
  )
  return parser.parse_args()


def build_list_url(word: str, page: int) -> str:
  return f"{BASE_URL}?word={quote(word)}&c=&page={page}"


def card_matches(time_text: str, within_hours: int) -> bool:
  """カードの表示時刻がスクレイピング対象かどうか。"""
  text = time_text.strip()
  minutes_match = re.fullmatch(r"(\d+)\s*分前", text)
  if minutes_match:
    return True
  hours_match = re.fullmatch(r"(\d+)\s*時間前", text)
  if hours_match:
    return int(hours_match.group(1)) <= within_hours
  return False


def main() -> int:
  args = parse_args()
  DATA_DIR.mkdir(parents=True, exist_ok=True)
  SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

  all_collected_urls: list[str] = []

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

      current_page = 1
      while True:
        url = build_list_url(args.word, current_page)
        print(f"\n--- Page {current_page} ---")
        print(f"Target URL: {url}")

        response = page.goto(
          url,
          timeout=args.timeout,
          wait_until="domcontentloaded",
        )
        status = response.status if response else None
        print(f"Status: {status if status is not None else 'No response'}")
        print(f"URL: {page.url}")

        if status is not None and status >= 400:
          print(
            f"Aborting: HTTP {status} が返されました。これまでの収集分で続行します。"
          )
          break

        try:
          page.wait_for_selector(
            LIST_CONTAINER_SELECTOR,
            state="attached",
            timeout=args.timeout,
          )
        except PlaywrightTimeoutError:
          print(
            f"一覧コンテナ {LIST_CONTAINER_SELECTOR} が見つかりませんでした。ページネーションを終了します。"
          )
          break

        cards: list[dict[str, str]] = page.evaluate(
          """(selector) => {
            const links = Array.from(document.querySelectorAll(selector));
            const seen = new Set();
            const result = [];
            for (const a of links) {
              const url = a.href;
              if (seen.has(url)) continue;
              seen.add(url);
              const itemWrapper = a.closest('.itemWrapper') || a.parentElement;
              const timeEl = itemWrapper ? itemWrapper.querySelector('.itemTime') : null;
              const time = timeEl ? timeEl.textContent.trim() : '';
              result.push({ url, time });
            }
            return result;
          }""",
          LIST_LINK_SELECTOR,
        )

        if not cards:
          print("カードが1件も取得できませんでした。ページネーションを終了します。")
          break

        page_urls: list[str] = []
        has_within_hours = False

        for c in cards:
          time_text = str(c.get("time") or "")
          if card_matches(time_text, args.within_hours):
            has_within_hours = True
            page_urls.append(c["url"])

        print(f"Extracted cards count: {len(cards)}")
        print(f"Matched URLs count in this page: {len(page_urls)}")
        print(f"Within hours: {args.within_hours}")

        all_collected_urls.extend(page_urls)

        if not has_within_hours:
          print(
            "指定時間以内のカードが見つからなかったため、ページネーションを終了します。"
          )
          break

        current_page += 1

      if not all_collected_urls:
        print(
          "Aborting: 条件に合致するURLを1件も取得できませんでした。既存データは変更しません。"
        )
        return 1

      existing = load_existing(DATA_PATH)
      result = merge_urls(existing, all_collected_urls)
      write_json_atomic(DATA_PATH, result)
      print(f"\nSaved {len(result)} total URLs to {DATA_PATH}")

      if page is not None:
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
