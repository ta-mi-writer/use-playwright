import argparse
import json
import random
import re
import shutil
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from urllib.parse import urlparse

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

MAX_ATTEMPTS_DEFAULT = 3
PAGE_DELAY_JITTER = 0.5
DMM_DETAIL_MARKER = "/digital/videoa/-/detail/=/cid="
CID_RE = re.compile(r"cid=([A-Za-z0-9_]+)")
VIDEO_SITES = (
  "txxx.com",
  "upornia.com",
  "hclips.com",
  "hotmovs.com",
  "hdzog.com",
  "vjav.com",
  "senzuri.tube",
)
VIDEO_PATH_RE = re.compile(r"^/videos/(\d+)/")
BLOG_LINK_TEXT_RE = re.compile(r"ページへ行く|\d+\s*分")
EXCLUDED_HOSTS = {
  "twitter.com",
  "x.com",
  "facebook.com",
  "b.hatena.ne.jp",
  "line.me",
  "google.com",
  "youtube.com",
}

LINK_POLL_INTERVAL_MS = 250
LINK_WAIT_DEFAULT_SECONDS = 6.0


PROCESSABLE_STATUSES = ("listed", "blog_resolved", "error", "partial")


class _DetailError(Exception):
  """詳細ページ取得中の制御用エラー（広域捕捉対象）。"""


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
  parser.add_argument(
    "--max-attempts",
    type=int,
    default=MAX_ATTEMPTS_DEFAULT,
    help="1アイテムあたりの最大試行回数",
  )
  parser.add_argument(
    "--page-delay",
    type=float,
    default=2.0,
    help="アイテム間待機秒（0以上）",
  )
  parser.add_argument(
    "--link-wait",
    type=float,
    default=LINK_WAIT_DEFAULT_SECONDS,
    help="リンク出現待ちの最大秒数",
  )
  parser.add_argument(
    "--retry-partial",
    action="store_true",
    help="partial 状態のアイテムも再処理する",
  )
  args = parser.parse_args()
  if args.page_delay < 0:
    parser.error("--page-delay は 0 以上を指定してください")
  if args.link_wait < 0:
    parser.error("--link-wait は 0 以上を指定してください")
  return args


def _host(url: str) -> str:
  return urlparse(url).hostname or ""


def _is_blog_host(host: str, article_host: str) -> bool:
  return (
    host != article_host
    and not host.endswith("eroterest.net")
    and host not in EXCLUDED_HOSTS
  )


def _is_video_host(host: str) -> str | None:
  """ホストが動画サイトと一致すればベースサイト名を返す。"""
  for site in VIDEO_SITES:
    if host == site or host.endswith("." + site):
      return site
  return None


def _normalize_video(href: str) -> dict[str, str | None]:
  """動画URLを表示用・同一性用・サイト名に正規化する。クエリ/フラグメントは捨てる。"""
  parsed = urlparse(href)
  host = parsed.hostname or ""
  path = parsed.path or ""
  site = _is_video_host(host)
  video_url = f"https://{host}{path}" if host else None
  if site and VIDEO_PATH_RE.match(path):
    m = VIDEO_PATH_RE.match(path)
    assert m is not None
    video_key = f"https://{site}/videos/{m.group(1)}/"
  elif site:
    video_key = f"https://{site}{path}"
  else:
    video_key = None
  return {
    "video_url": video_url,
    "video_key": video_key,
    "video_site": site,
  }


def _should_process(item: dict, max_attempts: int, retry_partial: bool = False) -> bool:
  if not isinstance(item, dict):
    return False
  status = item.get("status")
  if status not in PROCESSABLE_STATUSES:
    return False
  if status == "partial" and not retry_partial:
    return False
  return int(item.get("attempts", 0)) < max_attempts


def _now_iso() -> str:
  return datetime.now(UTC).isoformat()


def _extract_links(page) -> list[dict]:
  return page.evaluate(
    "() => Array.from(document.querySelectorAll('a')).map(a => ({href: a.href || '', text: a.textContent || ''}))"
  )


def resolve_blog_url(page, article_host: str) -> str | None:
  """エロタレスト記事ページから外部ブログURLを特定する。"""
  links = _extract_links(page)
  candidates: list[tuple[str, str]] = []
  for link in links:
    href = link.get("href") or ""
    text = link.get("text") or ""
    if not href.startswith(("http://", "https://")):
      continue
    host = _host(href)
    if not host or not _is_blog_host(host, article_host):
      continue
    candidates.append((href, text))
  if not candidates:
    return None

  freq = Counter(href for href, _ in candidates)
  best_href: str | None = None
  best_score = -1
  for href, text in candidates:
    score = freq[href] * 2
    if BLOG_LINK_TEXT_RE.search(text):
      score += 2
    if href.startswith("https://"):
      score += 1
    if score > best_score:
      best_score = score
      best_href = href
  return best_href


def extract_video_and_work(page) -> dict:
  """ブログ記事ページから動画URL・作品URL・CIDを抽出する。"""
  links = _extract_links(page)
  video: dict[str, str | None] | None = None
  work_url: str | None = None
  cid: str | None = None
  video_count = 0
  dmm_count = 0
  seen_cids: set[str] = set()

  for link in links:
    href = link.get("href") or ""
    host = _host(href)
    if not host:
      continue

    if _is_video_host(host):
      video_count += 1
      if video is None:
        video = _normalize_video(href)

    if DMM_DETAIL_MARKER in href:
      m = CID_RE.search(href)
      if m:
        dmm_count += 1
        seen_cids.add(m.group(1))
        if work_url is None:
          cid = m.group(1)
          work_url = "https://www.dmm.co.jp" + DMM_DETAIL_MARKER + m.group(1) + "/"

  return {
    "video_url": video["video_url"] if video else None,
    "video_key": video["video_key"] if video else None,
    "video_site": video["video_site"] if video else None,
    "work_url": work_url,
    "cid": cid,
    "video_count": video_count,
    "dmm_count": dmm_count,
    "seen_cids": seen_cids,
  }


def extract_with_wait(page, link_wait_seconds: float) -> tuple[dict, float]:
  """JSで遅延注入されるリンクを待ってから抽出する。"""
  extracted = extract_video_and_work(page)
  if extracted["video_url"] and extracted["work_url"]:
    return extracted, 0.0

  start = time.monotonic()
  while True:
    elapsed = time.monotonic() - start
    if elapsed >= link_wait_seconds:
      break
    page.wait_for_timeout(LINK_POLL_INTERVAL_MS)
    extracted = extract_video_and_work(page)
    if extracted["video_url"] and extracted["work_url"]:
      break

  return extracted, time.monotonic() - start


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

  # 受信箱マイグレーション（読み込み直後）
  bak_path = DATA_PATH.with_suffix(DATA_PATH.suffix + ".bak")
  migrated = False
  for item in items:
    if not isinstance(item, dict):
      continue
    if "status" not in item:
      item["status"] = "listed"
      migrated = True
    if "scraped" in item:
      del item["scraped"]
      migrated = True
    if "video_url_raw" in item:
      del item["video_url_raw"]
      migrated = True
    if "attempts" not in item:
      item["attempts"] = 0
      migrated = True

  if migrated and not bak_path.exists():
    shutil.copyfile(DATA_PATH, bak_path)
    print(f"Backup: {DATA_PATH} -> {bak_path}")

  target_indices = [
    i
    for i, item in enumerate(items)
    if _should_process(item, args.max_attempts, args.retry_partial)
  ]
  if not target_indices:
    print("処理対象のURLは存在しません。")
    return 0

  print(f"処理対象のURL件数: {len(target_indices)}")

  status_counter: Counter[str] = Counter()
  processed_count = 0
  with sync_playwright() as p:
    context = None
    try:
      context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROJECT_ROOT / ".browser-data"),
        headless=True,
        user_agent=args.user_agent,
      )
      page = context.new_page()

      for pos, idx in enumerate(target_indices):
        if args.limit > 0 and processed_count >= args.limit:
          print(f"指定された制限件数 ({args.limit}件) に達したため終了します。")
          break

        assert page is not None

        item = items[idx]
        url = item.get("url")
        if not isinstance(url, str):
          continue

        print(f"\n--- Detail [{processed_count + 1}] ---")
        print(f"URL: {url}")

        stage: str | None = None
        try:
          # S2a: エロタレスト記事 -> ブログURL
          if item.get("status") != "blog_resolved":
            stage = "s2a"
            response = page.goto(
              url, timeout=args.timeout, wait_until="domcontentloaded"
            )
            http_status = response.status if response else None
            if http_status is not None and http_status >= 400:
              raise _DetailError(f"HTTP {http_status}")
            blog_url = resolve_blog_url(page, _host(url))
            if blog_url is None:
              item["status"] = "unsupported"
              item["reason"] = "blog_link_not_found"
            else:
              item["blog_url"] = blog_url
              item["status"] = "blog_resolved"

          # S2b: ブログ記事 -> 動画URL・作品URL
          if item.get("status") == "blog_resolved":
            stage = "s2b"
            blog_url = item.get("blog_url")
            if not isinstance(blog_url, str) or not blog_url:
              raise _DetailError("blog_url が空です")
            response = page.goto(
              blog_url, timeout=args.timeout, wait_until="domcontentloaded"
            )
            http_status = response.status if response else None
            if http_status is not None and http_status >= 400:
              raise _DetailError(f"HTTP {http_status}")
            extracted, waited = extract_with_wait(page, args.link_wait)
            if extracted["video_url"] and extracted["work_url"]:
              if waited > 0:
                print(f"リンク出現を待機しました ({waited:.1f}s)")
            else:
              print(f"リンク未検出（待機 {waited:.1f}s）")
            if extracted["video_count"] > 1:
              print(
                f"Warning: 動画リンクが複数あります ({extracted['video_count']})。先頭を採用: {url}"
              )
            if len(extracted["seen_cids"]) > 1:
              print(
                f"Warning: CID が複数種類あります ({len(extracted['seen_cids'])})。先頭を採用: {url}"
              )
            if extracted["video_url"]:
              item["video_url"] = extracted["video_url"]
              item["video_key"] = extracted["video_key"]
              item["video_site"] = extracted["video_site"]
            if extracted["work_url"]:
              item["work_url"] = extracted["work_url"]
              item["cid"] = extracted["cid"]
            reasons: list[str] = []
            if not item.get("video_url"):
              reasons.append("missing_video_url")
            if not item.get("work_url"):
              reasons.append("missing_work_url")
            if not reasons:
              item["status"] = "links_done"
              item.pop("reason", None)
              item.pop("last_error", None)
              item.pop("stage", None)
            elif len(reasons) == 1:
              item["status"] = "partial"
              item["reason"] = reasons[0]
            else:
              item["status"] = "unsupported"
              item["reason"] = ", ".join(reasons)

          item["updated_at"] = _now_iso()
          processed_count += 1
          status_counter[item["status"]] += 1

          print(
            f"URL: {url} | status: {item['status']} | reason: {item.get('reason', '-')} | "
            f"blog_url: {item.get('blog_url', '-')} | "
            f"video_url: {item.get('video_url', '-')} | "
            f"video_key: {item.get('video_key', '-')} | "
            f"video_site: {item.get('video_site', '-')} | "
            f"work_url: {item.get('work_url', '-')} | "
            f"cid: {item.get('cid', '-')}"
          )

          write_json_atomic(DATA_PATH, items)

        except Exception as e:  # noqa: BLE001 - 想定外例外もステータス化して継続
          attempts = int(item.get("attempts", 0)) + 1
          item["attempts"] = attempts
          item["status"] = "dead" if attempts >= args.max_attempts else "error"
          item["last_error"] = str(e)
          item["stage"] = stage
          item["updated_at"] = _now_iso()
          status_counter[item["status"]] += 1
          processed_count += 1
          print(
            f"URL: {url} | status: {item['status']} | error: {e} | "
            f"stage: {stage} | attempts: {attempts}"
          )
          write_json_atomic(DATA_PATH, items)

        # アイテム間待機（最後以外）
        is_last = pos == len(target_indices) - 1
        limit_reached = args.limit > 0 and processed_count >= args.limit
        if not is_last and not limit_reached and page is not None:
          delay_sec = max(
            0.0, args.page_delay + random.uniform(-PAGE_DELAY_JITTER, PAGE_DELAY_JITTER)
          )
          page.wait_for_timeout(int(delay_sec * 1000))
          print(f"wait {delay_sec:.1f}s")

      print("\n=== 結果サマリー ===")
      for status, count in status_counter.items():
        print(f"{status}: {count}")
      print(f"処理件数: {processed_count}")

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
