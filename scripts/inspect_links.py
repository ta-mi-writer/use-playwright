#!/usr/bin/env python3
"""eroterest 記事ページから外部リンクを読み取り専用で調査する。

JSON ファイルは読み取りのみ。書き換えは行わない。
--url 指定時は JSON 不要で 1 URL を --details 相当で検査する。
--details 指定時は、1ページの読み込みでリンク・iframe・動画・ネットワークリクエスト等を詳しく収集する。
--link-domain で対象ドメインをカンマ区切りで指定可能（デフォルトは dmm.co.jp/dmm.com/fanza）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
  sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright

from utils import DEFAULT_USER_AGENT
from utils import PROJECT_ROOT as UTILS_PROJECT_ROOT

BROWSER_DATA_DIR = UTILS_PROJECT_ROOT / ".browser-data"
FANZA_KEYWORDS = ("dmm.co.jp", "dmm.com", "fanza")
VJAV_KEYWORD = "vjav.com"
DEFAULT_LINK_DOMAINS = ",".join(FANZA_KEYWORDS)
MAX_PAGES = 4
INTER_PAGE_WAIT_MS = 2000
PAGE_WAIT_MS = 2000
TOTAL_TIMEOUT_S = 300

# 年齢確認・同意オーバーレイ判定用キーワード
AGE_GATE_KEYWORDS = (
  "18歳",
  "年齢確認",
  "アダルト",
  "成人",
  "同意",
  "入場",
  "ENTER",
  "はい",
  "いいえ",
)

# テキスト/aria-label 照合用正規表現
LINK_TEXT_RE = re.compile(r"FANZA|DMM|作品|動画|サンプル|配信|購入|見る", re.IGNORECASE)

# DMM/FANZA 作品詳細ページのパスパターン
DETAIL_PATH_RE = re.compile(r"/digital/videoa/-/detail/=/cid=([A-Za-z0-9]+)")
LITEVIDEO_PATH_RE = re.compile(r"/litevideo/")


def mask_url(url: str) -> str:
  """URL中の数字ID系を <ID> に置換して伏せ字にする。"""
  return re.sub(r"\d{4,}", "<ID>", url)


def domain_of(url: str) -> str:
  """URLからホスト部分を抽出（失敗時は空文字）。"""
  return urlparse(url).netloc or ""


def is_vjav_url(url: str) -> bool:
  """vjav.com（サブドメイン含む）かどうかを判定する。"""
  return VJAV_KEYWORD in (urlparse(url).netloc or "").lower()


def classify_dmm_link(href: str) -> str:
  """dmm/fanza リンクをパスパターンで分類する。"""
  low = href.lower()
  if DETAIL_PATH_RE.search(low):
    return "作品詳細"
  if LITEVIDEO_PATH_RE.search(low):
    return "サンプル動画"
  return "その他"


def select_indices(
  items: list[dict[str, Any]], count: int, indices: list[int] | None
) -> list[int]:
  """調査対象インデックスを選ぶ。--indices 指定時はそれを優先し、未指定時は等間隔で選ぶ。"""
  if indices:
    return [i for i in indices if 0 <= i < len(items)]
  n = len(items)
  if n == 0:
    return []
  if count >= n:
    return list(range(n))
  if count <= 1:
    return [0]
  selected = {0, n - 1}
  step = (n - 1) / (count - 1)
  for k in range(1, count - 1):
    selected.add(round(k * step))
  return sorted(selected)


def extract_links(page, keywords=FANZA_KEYWORDS) -> list[dict[str, str]]:
  """ページ内の <a> から指定キーワードを含む href を重複排除して抽出。"""
  anchors = page.query_selector_all("a")
  seen: set[str] = set()
  results: list[dict[str, str]] = []
  low_keywords = [k.lower() for k in keywords]
  for anchor in anchors:
    try:
      href = anchor.get_attribute("href") or ""
    except Exception:  # noqa: BLE001, S112
      continue
    if not href:
      continue
    low = href.lower()
    if not any(k in low for k in low_keywords):
      continue
    if href in seen:
      continue
    seen.add(href)
    try:
      text = anchor.inner_text().strip()
    except Exception:  # noqa: BLE001
      text = ""
    results.append({"href": href, "text": text})
  return results


def collect_vjav_details(page, clue_url: str | None) -> dict[str, Any]:
  """VJAV 専用の詳細情報を収集する。"""
  details: dict[str, Any] = {}

  # --- 1. vjav.com を含む <a> ---
  vjav_links: list[dict[str, str]] = []
  seen: set[str] = set()
  for anchor in page.query_selector_all("a"):
    try:
      href = anchor.get_attribute("href") or ""
    except Exception:  # noqa: BLE001, S112
      continue
    if not href or VJAV_KEYWORD not in href.lower():
      continue
    if href in seen:
      continue
    seen.add(href)
    try:
      text = anchor.inner_text().strip()[:40]
    except Exception:  # noqa: BLE001
      text = ""
    vjav_links.append({"href": href, "text": text})
  details["vjav_links"] = vjav_links[:10]
  details["vjav_link_count"] = len(vjav_links)

  # --- 2. 手がかりURLの完全一致 ---
  details["clue_url"] = clue_url
  details["clue_url_found"] = bool(
    clue_url and any(l["href"] == clue_url for l in vjav_links)
  )

  # --- 3. <iframe> src に vjav ---
  vjav_iframes: list[str] = []
  for iframe in page.query_selector_all("iframe"):
    try:
      src = iframe.get_attribute("src") or ""
    except Exception:  # noqa: BLE001, S112
      continue
    if src and VJAV_KEYWORD in src.lower():
      vjav_iframes.append(src)
  details["vjav_iframes"] = vjav_iframes[:5]

  # --- 4. <video>/<source> と .mp4/.m3u8 ---
  has_media = False
  for tag in ("video", "source"):
    for el in page.query_selector_all(tag):
      try:
        src = el.get_attribute("src") or ""
        data_src = el.get_attribute("data-src") or ""
      except Exception:  # noqa: BLE001, S112
        continue
      if src or data_src:
        has_media = True
        break
    if has_media:
      break
  if not has_media:
    for el in page.query_selector_all("[src]"):
      try:
        src = el.get_attribute("src") or ""
      except Exception:  # noqa: BLE001, S112
        continue
      low = src.lower()
      if ".mp4" in low or ".m3u8" in low:
        has_media = True
        break
  details["has_video_media"] = has_media

  # --- 5. dmm.co.jp 作品詳細リンク ---
  dmm_detail_count = 0
  for anchor in page.query_selector_all("a"):
    try:
      href = anchor.get_attribute("href") or ""
    except Exception:  # noqa: BLE001, S112
      continue
    if "/digital/videoa/-/detail/=/cid=" in href:
      dmm_detail_count += 1
  details["dmm_detail_count"] = dmm_detail_count

  return details


def collect_details(page, link_domain_keywords, clue_url=None) -> dict[str, Any]:
  """--details モード用：1ページの読み込みで詳細情報を収集する。"""
  details: dict[str, Any] = {}

  # --- 1. 全 <a> href をドメイン単位で集計 ---
  domain_map: dict[str, dict[str, Any]] = {}
  anchors = page.query_selector_all("a")
  for anchor in anchors:
    try:
      href = anchor.get_attribute("href") or ""
    except Exception:  # noqa: BLE001, S112
      continue
    if not href:
      continue
    dom = domain_of(href)
    if dom not in domain_map:
      domain_map[dom] = {"count": 0, "example": href}
    domain_map[dom]["count"] += 1
  details["domains"] = {
    dom: {"count": info["count"], "example": mask_url(info["example"])}
    for dom, info in sorted(domain_map.items(), key=lambda x: -x[1]["count"])
  }

  # --- 2. テキスト/aria-label がキーワードに一致する <a>/<button> ---
  matched: list[dict[str, Any]] = []
  for tag in ("a", "button"):
    els = page.query_selector_all(tag)
    for el in els:
      try:
        text = (el.inner_text() or "").strip()
        aria = el.get_attribute("aria-label") or ""
      except Exception:  # noqa: BLE001, S112
        continue
      if not LINK_TEXT_RE.search(text) and not LINK_TEXT_RE.search(aria):
        continue
      link_entry: dict[str, Any] = {
        "tag": tag,
        "text": text[:40],
      }
      if aria:
        link_entry["aria_label"] = aria[:40]
      try:
        href = el.get_attribute("href") or ""
        if href:
          link_entry["href"] = mask_url(href)
      except Exception:  # noqa: BLE001, S110
        pass
      try:
        onclick = el.get_attribute("onclick") or ""
        if onclick:
          link_entry["onclick"] = onclick[:120]
      except Exception:  # noqa: BLE001, S110
        pass
      matched.append(link_entry)
      if len(matched) >= 20:
        break
    if len(matched) >= 20:
      break
  details["keyword_links"] = matched

  # --- 3. 全 <iframe> の src ---
  iframes: list[str] = []
  for iframe in page.query_selector_all("iframe"):
    try:
      src = iframe.get_attribute("src") or ""
    except Exception:  # noqa: BLE001, S112
      continue
    if src:
      iframes.append(mask_url(src))
  details["iframes"] = iframes

  # --- 4. <video>/<source> と .mp4/.m3u8 を含む src/data-src 要素 ---
  media: list[dict[str, Any]] = []
  for tag in ("video", "source"):
    for el in page.query_selector_all(tag):
      try:
        tag_name = el.evaluate("el => el.tagName.toLowerCase()")
        src = el.get_attribute("src") or ""
        data_src = el.get_attribute("data-src") or ""
      except Exception:  # noqa: BLE001, S112
        continue
      if not src and not data_src:
        continue
      entry: dict[str, Any] = {"tag": tag_name}
      if src:
        entry["src"] = mask_url(src)
      if data_src:
        entry["data_src"] = mask_url(data_src)
      media.append(entry)
  details["media"] = media

  # --- 5. ネットワークリクエスト（対象ドメイン/.mp4/.m3u8）---
  network_urls: list[str] = []

  def on_request(request):
    try:
      url = request.url
    except Exception:  # noqa: BLE001
      return
    low = url.lower()
    if (
      any(k in low for k in link_domain_keywords)
      or any(k in low for k in (".mp4", ".m3u8"))
    ) and url not in network_urls:
      network_urls.append(url)

  page.on("request", on_request)
  details["network_requests_collector"] = network_urls

  # --- 6. インライン <script> に対象ドメインが含まれるか ---
  inline_has_target = False
  for script in page.query_selector_all("script"):
    try:
      src = script.get_attribute("src") or ""
      if src:
        continue  # 外部スクリプトは除外
      content = script.inner_text() or ""
    except Exception:  # noqa: BLE001, S112
      continue
    low = content.lower()
    if any(k in low for k in link_domain_keywords):
      inline_has_target = True
      break
  details["inline_script_has_target"] = inline_has_target

  # --- 7. 年齢確認らしきオーバーレイ/同意ボタン ---
  age_gate_found = False
  age_gate_text = ""
  try:
    body_text = page.inner_text("body") or ""
  except Exception:  # noqa: BLE001
    body_text = ""
  for kw in AGE_GATE_KEYWORDS:
    if kw in body_text:
      age_gate_found = True
      age_gate_text = kw
      break
  details["age_gate"] = {
    "found": age_gate_found,
    "keyword": age_gate_text,
  }

  # --- 8. h1 と主要 h2 (最大3件、各40文字まで) ---
  headings: list[str] = []
  try:
    h1 = page.query_selector("h1")
    if h1:
      t = (h1.inner_text() or "").strip()
      if t:
        headings.append(f"h1: {t[:40]}")
  except Exception:  # noqa: BLE001, S110
    pass
  try:
    h2s = page.query_selector_all("h2")
    for h2 in h2s:
      t = (h2.inner_text() or "").strip()
      if t:
        headings.append(f"h2: {t[:40]}")
      if len(headings) >= 3:
        break
  except Exception:  # noqa: BLE001, S110
    pass
  details["headings"] = headings

  # --- 9. VJAV 専用詳細 ---
  if VJAV_KEYWORD in [k.lower() for k in link_domain_keywords]:
    details["vjav"] = collect_vjav_details(page, clue_url)

  return details


def inspect_single(
  url: str,
  timeout: int,
  details: bool,
  link_domain_keywords,
  clue_url: str | None = None,
) -> dict[str, Any]:
  """1 URL を読み取り専用で検査し、結果を辞書で返す。"""
  result: dict[str, Any] = {"url": url}

  with sync_playwright() as p:
    context = None
    try:
      context = p.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_DATA_DIR),
        headless=True,
        user_agent=DEFAULT_USER_AGENT,
      )
      page = context.new_page()
      try:
        response = page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        status = response.status if response else None
        page.wait_for_timeout(PAGE_WAIT_MS)
        final_url = page.url
        try:
          title = page.title()
        except Exception:  # noqa: BLE001
          title = ""
        result["status"] = status
        result["final_url"] = final_url
        result["title"] = title

        if details:
          network_urls: list[str] = []

          def on_request(request):
            try:
              u = request.url
            except Exception:  # noqa: BLE001
              return
            low = u.lower()
            if (
              any(k in low for k in link_domain_keywords)
              or any(k in low for k in (".mp4", ".m3u8"))
            ) and u not in network_urls:
              network_urls.append(u)

          page.on("request", on_request)
          details_data = collect_details(page, link_domain_keywords, clue_url)
          details_data["network_requests"] = [mask_url(u) for u in network_urls[:10]]
          result["details"] = details_data

        links = extract_links(page, link_domain_keywords)
        result["links"] = links
      except Exception as e:  # noqa: BLE001 - 失敗は1行で原因報告
        result["error"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
      result["error"] = f"ブラウザ起動失敗 -> {type(e).__name__}: {e}"
    finally:
      if context is not None:
        context.close()
  return result


def run() -> int:
  parser = argparse.ArgumentParser(
    description="eroterest-page.json の記事URLから外部リンクを調査する（読み取り専用）"
  )
  parser.add_argument("--json", required=False, help="対象JSONファイルのパス")
  parser.add_argument(
    "--url",
    required=False,
    help="単一URLを直接指定して検査する（--json 不要）",
  )
  parser.add_argument(
    "--link-domain",
    required=False,
    default=DEFAULT_LINK_DOMAINS,
    help="対象ドメインをカンマ区切りで指定（デフォルト: dmm.co.jp,dmm.com,fanza）",
  )
  parser.add_argument(
    "--clue-url",
    required=False,
    help="手がかりURL（完全一致チェック用）",
  )
  parser.add_argument(
    "--count", type=int, default=3, help="調査する記事の最大件数（デフォルト 3）"
  )
  parser.add_argument(
    "--indices",
    default=None,
    help="調査対象のインデックスをカンマ区切りで指定（--count より優先）",
  )
  parser.add_argument(
    "--timeout", type=int, default=30000, help="goto タイムアウト(ms)"
  )
  parser.add_argument(
    "--details",
    action="store_true",
    help="1ページの詳細情報（ドメイン集計・キーワードリンク・iframe・動画・ネットワーク・インラインスクリプト・年齢確認・見出し・VJAV専用）を収集する",
  )
  args = parser.parse_args()

  link_domain_keywords = tuple(
    k.strip() for k in args.link_domain.split(",") if k.strip()
  )

  def print_details(d: dict[str, Any], is_vjav: bool) -> None:
    print("\n[詳細] ドメイン別 <a> 集計 (上位10):")
    for dom, info in list(d["domains"].items())[:10]:
      print(f"  {dom}: {info['count']}件 例: {info['example']}")
    print(f"[詳細] キーワード一致 <a>/<button>: {len(d['keyword_links'])} 件")
    for m in d["keyword_links"][:10]:
      parts = [f"{m['tag']} text={m['text']!r}"]
      if "href" in m:
        parts.append(f"href={m['href']}")
      if "onclick" in m:
        parts.append(f"onclick={m['onclick']}")
      print("  - " + " | ".join(parts))
    print(f"[詳細] <iframe> src: {len(d['iframes'])} 件")
    for s in d["iframes"]:
      print(f"  - {s}")
    print(f"[詳細] <video>/<source> 等: {len(d['media'])} 件")
    for m in d["media"]:
      print(f"  - {m}")
    print(f"[詳細] ネットワークリクエスト: {len(d['network_requests'])} 件")
    for u in d["network_requests"]:
      print(f"  - {u}")
    print(f"[詳細] インライン<script>に対象ドメイン: {d['inline_script_has_target']}")
    print(f"[詳細] 年齢確認/同意オーバーレイ: {d['age_gate']}")
    print(f"[詳細] 見出し: {d['headings']}")
    if is_vjav and "vjav" in d:
      v = d["vjav"]
      print(f"\n[VJAV] リンク件数: {v['vjav_link_count']}")
      print("[VJAV] リンク (最大10):")
      for l in v["vjav_links"]:
        print(f"  - {l['href']} | {l['text']}")
      print(f"[VJAV] 手がかりURL完全一致: {v['clue_url_found']}")
      print(f"[VJAV] iframe: {len(v['vjav_iframes'])} 件")
      for s in v["vjav_iframes"]:
        print(f"  - {s}")
      vjav_network = [u for u in d["network_requests"] if "vjav" in u.lower()]
      print(f"[VJAV] ネットワークリクエスト(vjav): {len(vjav_network)} 件 (最大5)")
      for u in vjav_network[:5]:
        print(f"  - {u}")
      print(f"[VJAV] 動画メディア(video/source/mp4/m3u8): {v['has_video_media']}")
      print(
        f"[DMM] 作品詳細リンク(/digital/videoa/-/detail/=/cid=): {v['dmm_detail_count']}"
      )

  if args.url:
    result = inspect_single(
      args.url, args.timeout, args.details, link_domain_keywords, args.clue_url
    )
    if "error" in result:
      print(f"Error: {result['error']}")
      return 1
    print(f"URL: {result['url']}")
    print(f"Status: {result.get('status')}")
    print(f"Final URL: {result.get('final_url')}")
    print(f"Title: {result.get('title')}")
    is_vjav = VJAV_KEYWORD in [k.lower() for k in link_domain_keywords]
    if args.details and "details" in result:
      print_details(result["details"], is_vjav)
    print(f"\n対象リンク候補: {len(result['links'])} 件")
    for link in result["links"]:
      print(f"  - {link['href']} | {link['text'][:80]}")
    return 0

  json_path = Path(args.json)
  if not json_path or not json_path.exists():
    print(f"Error: JSONファイルが見つかりません: {json_path}")
    return 1

  try:
    with json_path.open("r", encoding="utf-8") as f:
      items = json.load(f)
  except (json.JSONDecodeError, OSError) as e:
    print(f"Error: JSONファイルを読み込めませんでした: {e}")
    return 1

  if not isinstance(items, list):
    print("Error: JSONデータの形式が不正です（リストである必要があります）。")
    return 1

  indices = (
    [int(x) for x in args.indices.split(",") if x.strip()] if args.indices else None
  )
  target_indices = select_indices(items, args.count, indices)
  if not target_indices:
    print("調査対象のインデックスがありません。")
    return 1

  # URL が重複しない代表エントリを選び、最大 MAX_PAGES に留める
  seen_urls: set[str] = set()
  unique_entries: list[tuple[int, str]] = []
  for i in target_indices:
    item = items[i]
    if not isinstance(item, dict):
      continue
    url = item.get("url")
    if not isinstance(url, str) or not url:
      continue
    if url in seen_urls:
      continue
    seen_urls.add(url)
    unique_entries.append((i, url))
  unique_entries = unique_entries[:MAX_PAGES]

  print(f"対象JSON: {json_path}")
  print(
    f"調査対象記事数: {len(unique_entries)} (インデックス: {[i for i, _ in unique_entries]})"
  )
  print("=" * 60)

  all_unique_links: dict[str, str] = {}  # href -> text (first seen)

  with sync_playwright() as p:
    context = None
    try:
      context = p.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_DATA_DIR),
        headless=True,
        user_agent=DEFAULT_USER_AGENT,
      )
      page = context.new_page()

      for pos, (idx, url) in enumerate(unique_entries, start=1):
        print(f"\n--- [{pos}/{len(unique_entries)}] インデックス {idx} ---")
        print(f"URL: {url}")
        try:
          response = page.goto(url, timeout=args.timeout, wait_until="domcontentloaded")
          status = response.status if response else None
          page.wait_for_timeout(PAGE_WAIT_MS)
          final_url = page.url
          try:
            title = page.title()
          except Exception:  # noqa: BLE001
            title = ""
          print(f"Status: {status}")
          print(f"Final URL: {final_url}")
          print(f"Title: {title}")

          is_vjav = VJAV_KEYWORD in [k.lower() for k in link_domain_keywords]
          if args.details:
            network_urls: list[str] = []

            def on_request(request, _network_urls=network_urls):
              try:
                u = request.url
              except Exception:  # noqa: BLE001
                return
              low = u.lower()
              if (
                any(k in low for k in link_domain_keywords)
                or any(k in low for k in (".mp4", ".m3u8"))
              ) and u not in _network_urls:
                _network_urls.append(u)

            page.on("request", on_request)
            details = collect_details(page, link_domain_keywords, args.clue_url)
            details["network_requests"] = [mask_url(u) for u in network_urls[:10]]

            print_details(details, is_vjav)

          links = extract_links(page, link_domain_keywords)
          print(f"対象リンク候補: {len(links)} 件")
          for link in links:
            print(f"  - {link['href']} | {link['text'][:80]}")
            if link["href"] not in all_unique_links:
              all_unique_links[link["href"]] = link["text"]

        except Exception as e:  # noqa: BLE001 - 失敗は1行で原因報告
          print(f"Error: {url} -> {type(e).__name__}: {e}")

        # ページ間待機（最後以外）
        if pos < len(unique_entries):
          page.wait_for_timeout(INTER_PAGE_WAIT_MS)

    except Exception as e:  # noqa: BLE001
      print(f"Error: ブラウザ起動失敗 -> {type(e).__name__}: {e}")
      return 1
    finally:
      if context is not None:
        context.close()

  print("\n" + "=" * 60)
  print("【一意な外部リンク（href とテキスト）】")
  for href, text in all_unique_links.items():
    print(f"- {href} | {text[:100]}")
  print(f"合計: {len(all_unique_links)} 件")

  return 0


if __name__ == "__main__":
  sys.exit(run())
