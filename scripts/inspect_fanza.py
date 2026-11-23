#!/usr/bin/env python3
"""FANZA 作品ページの年齢確認・レビューDOM構造を読み取り専用で調査する（動的レビュー読み込み対応版）。

--url で 1 URL を指定する。
既存 scripts/inspect_links.py の起動方式に合わせる。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
  sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright

from utils import DEFAULT_USER_AGENT
from utils import PROJECT_ROOT as UTILS_PROJECT_ROOT

BROWSER_DATA_DIR = UTILS_PROJECT_ROOT / ".browser-data"
PAGE_WAIT_MS = 2000
GOTO_TIMEOUT_MS = 30000
SCROLL_WAIT_MS = 3000
MORE_CLICK_WAIT_MS = 3000

AGE_GATE_URL_KEYWORDS = ("age_check", "agecheck")
AGE_GATE_TITLE_KEYWORDS = ("年齢認証", "年齢確認")

REVIEW_COUNT_PATTERNS = [
  r"レビュー\s*(\d{1,6})\s*件",
  r"(\d{1,6})\s*件\s*のレビュー",
  r"口コミ\s*(\d{1,6})\s*件",
  r"(\d{1,6})\s*件\s*の口コミ",
]

DATE_RE = re.compile(r"\d{4}[/年]\d{1,2}[/月]\d{1,2}日?")

MORE_BUTTON_SELECTORS = [
  'button:has-text("もっと")',
  'a:has-text("もっと")',
  'button:has-text("レビューをもっと見る")',
  'a:has-text("レビューをもっと見る")',
  'button:has-text("続きを表示")',
  'a:has-text("続きを表示")',
]


def body_text(page) -> str:
  try:
    return page.evaluate("() => (document.body && document.body.innerText) || ''") or ""
  except Exception:  # noqa: BLE001
    return ""


def is_real_age_gate(page) -> bool:
  url = page.url.lower()
  title = page.title()
  return any(k in url for k in AGE_GATE_URL_KEYWORDS) or any(
    k in title for k in AGE_GATE_TITLE_KEYWORDS
  )


def detect_age_gate(page) -> dict[str, Any]:
  return {
    "detected": is_real_age_gate(page),
    "url": page.url,
    "title": page.title(),
  }


def dump_age_check_page(page) -> dict[str, Any]:
  """年齢確認ページのフォーム/ボタン/リンク構造を要約する。"""
  inputs: list[dict[str, str]] = []
  buttons: list[dict[str, str]] = []
  links: list[dict[str, str]] = []
  try:
    for el in page.query_selector_all("input")[:60]:
      inputs.append(
        {
          "type": el.get_attribute("type") or "",
          "name": el.get_attribute("name") or "",
          "value": el.get_attribute("value") or "",
          "text": (el.inner_text() or "").strip()[:40],
        }
      )
  except Exception:  # noqa: BLE001, S110
    pass
  try:
    for el in page.query_selector_all("button")[:60]:
      buttons.append(
        {
          "type": el.get_attribute("type") or "",
          "name": el.get_attribute("name") or "",
          "value": el.get_attribute("value") or "",
          "text": (el.inner_text() or "").strip()[:60],
        }
      )
  except Exception:  # noqa: BLE001, S110
    pass
  try:
    for el in page.query_selector_all("a")[:80]:
      href = el.get_attribute("href") or ""
      txt = (el.inner_text() or "").strip()
      if (
        any(k in txt for k in ("はい", "いいえ", "ENTER", "入場", "18"))
        or "age" in href.lower()
      ):
        links.append({"href": href, "text": txt[:60]})
  except Exception:  # noqa: BLE001, S110
    pass
  return {"inputs": inputs, "buttons": buttons, "links": links}


def try_bypass_age_gate(page) -> dict[str, Any]:
  """年齢確認ページで「はい」を選択して突破を試みる。"""
  result: dict[str, Any] = {"method": None, "selector": None, "clicked_commit": False}
  yes_sel: str | None = None
  for sel in [
    'input[type="radio"]:has-text("はい")',
    'label:has-text("はい")',
    'button:has-text("はい")',
    'a:has-text("はい")',
  ]:
    try:
      loc = page.query_selector(sel)
      if loc is not None:
        loc.click()
        yes_sel = sel
        break
    except Exception:  # noqa: BLE001, S112
      continue
  result["method"] = "select_yes"
  result["selector"] = yes_sel
  page.wait_for_timeout(PAGE_WAIT_MS)
  for sel in [
    'input[type="image"][name="commit"]',
    'button:has-text("入場")',
    'a:has-text("入場")',
    'button:has-text("ENTER")',
    'a:has-text("ENTER")',
  ]:
    try:
      loc = page.query_selector(sel)
      if loc is not None:
        loc.click()
        result["clicked_commit"] = True
        result["commit_selector"] = sel
        break
    except Exception:  # noqa: BLE001, S112
      continue
  page.wait_for_timeout(PAGE_WAIT_MS)
  return result


def scroll_to_review(page) -> dict[str, Any]:
  """#review セクションまでスクロールし、動的読み込みを待つ。"""
  result: dict[str, Any] = {"scrolled": False, "review_visible": False}
  try:
    section = page.query_selector("#review")
    if section is not None:
      section.scroll_into_view_if_needed()
      result["scrolled"] = True
      page.wait_for_timeout(SCROLL_WAIT_MS)
      result["review_visible"] = section.is_visible()
  except Exception:  # noqa: BLE001, S110
    pass
  return result


def click_more_buttons(page) -> list[str]:
  """「レビューをもっと見る」等のボタンをクリックする。"""
  clicked: list[str] = []
  for sel in MORE_BUTTON_SELECTORS:
    try:
      loc = page.query_selector(sel)
      if loc is not None and loc.is_visible():
        loc.click()
        clicked.append(sel)
        page.wait_for_timeout(MORE_CLICK_WAIT_MS)
    except Exception:  # noqa: BLE001, S112
      continue
  return clicked


def find_review_count(page) -> dict[str, Any] | None:
  text = body_text(page)
  for pat in REVIEW_COUNT_PATTERNS:
    m = re.search(pat, text)
    if m:
      return {"pattern": pat, "count": int(m.group(1)), "text": m.group(0)}
  m = re.search(r"\(\s*(\d{1,6})\s*\)", body_text(page))
  if m:
    return {"pattern": r"\((\d+)\)", "count": int(m.group(1)), "text": m.group(0)}
  return None


def investigate_review_section(page) -> dict[str, Any]:
  """#review セクション内の個別レビュー構造を深掘りする。"""
  info: dict[str, Any] = {"section": None, "reviews": [], "section_text": ""}
  try:
    section = page.query_selector("#review")
  except Exception:  # noqa: BLE001
    section = None
  if section is None:
    return info
  try:
    info["section"] = {
      "tag": section.evaluate("el => el.tagName") or "",
      "id": section.get_attribute("id") or "",
      "class": section.get_attribute("class") or "",
    }
    info["section_text"] = (section.inner_text() or "").strip()[:2500]
  except Exception:  # noqa: BLE001, S110
    pass
  try:
    items = section.query_selector_all("div")[:80]
  except Exception:  # noqa: BLE001
    items = []
  seen: set[str] = set()
  for it in items:
    try:
      cls = it.get_attribute("class") or ""
      it_id = it.get_attribute("id") or ""
      txt = (it.inner_text() or "").strip()
      key = f"div:{cls}:{it_id}"
      if key in seen or len(txt) < 8:
        continue
      seen.add(key)
      links: list[str] = []
      try:
        for a in it.query_selector_all("a[href]")[:5]:
          href = a.get_attribute("href") or ""
          if href:
            links.append(href)
      except Exception:  # noqa: BLE001, S110
        pass
      info["reviews"].append(
        {"class": cls, "id": it_id, "text": txt[:140], "links": links[:5]}
      )
    except Exception:  # noqa: BLE001, S112
      continue
  return info


def find_individual_reviews(page) -> list[dict[str, Any]]:
  """日付を含む要素から個別レビューのコンテナを推定し、構造を収集する。"""
  reviews: list[dict[str, Any]] = []
  try:
    candidates = page.query_selector_all("div, section, article, li")[:300]
  except Exception:  # noqa: BLE001
    candidates = []
  date_els = []
  for el in candidates:
    try:
      txt = (el.inner_text() or "").strip()
      if DATE_RE.search(txt) and 10 < len(txt) < 3000:
        date_els.append(el)
    except Exception:  # noqa: BLE001, S112
      continue
  seen: set[str] = set()
  for el in date_els:
    container = el
    try:
      for _ in range(5):
        parent = container.evaluate_handle("el => el.parentElement")
        if parent is None:
          break
        try:
          has_star = parent.query_selector("img[src*='star']") is not None
        except Exception:  # noqa: BLE001
          has_star = False
        if has_star:
          container = parent
          break
        container = parent
    except Exception:  # noqa: BLE001, S110
      pass
    try:
      cls = container.get_attribute("class") or ""
      if cls in seen:
        continue
      seen.add(cls)
      txt = (container.inner_text() or "").strip()
      dm = DATE_RE.search(txt)
      date = dm.group(0) if dm else ""
      links: list[str] = []
      for a in container.query_selector_all("a[href]")[:10]:
        href = a.get_attribute("href") or ""
        if href:
          links.append(href)
      stars: list[str] = []
      for img in container.query_selector_all("img[src*='star']")[:10]:
        src = img.get_attribute("src") or ""
        if src:
          stars.append(src)
      reviews.append(
        {
          "class": cls,
          "date": date,
          "body": txt[:200],
          "links": links,
          "stars": stars,
        }
      )
    except Exception:  # noqa: BLE001, S112
      continue
  return reviews[:20]


def discover_review_elements(page) -> list[dict[str, str]]:
  out: list[dict[str, str]] = []
  selectors = ['[class*="review"]', '[id*="review"]', '[class*="Review"]']
  seen: set[str] = set()
  for sel in selectors:
    try:
      els = page.query_selector_all(sel)
    except Exception:  # noqa: BLE001, S112
      continue
    for el in els[:20]:
      try:
        cls = el.get_attribute("class") or ""
        tag = el.evaluate("el => el.tagName") or ""
        el_id = el.get_attribute("id") or ""
        key = f"{tag}:{cls}:{el_id}"
        if key in seen:
          continue
        seen.add(key)
        txt = (el.inner_text() or "").strip()
        out.append({"tag": tag, "id": el_id, "class": cls, "text": txt[:80]})
      except Exception:  # noqa: BLE001, S112
        continue
  return out[:20]


def find_review_links(page) -> list[dict[str, str]]:
  out: list[dict[str, str]] = []
  try:
    els = page.query_selector_all('a[href*="review"], a[href*="Review"]')
  except Exception:  # noqa: BLE001
    els = []
  for el in els[:20]:
    try:
      href = el.get_attribute("href") or ""
      txt = (el.inner_text() or "").strip()
      out.append({"href": href, "text": txt[:60]})
    except Exception:  # noqa: BLE001, S112
      continue
  return out


def discover_star_and_date(page) -> dict[str, Any]:
  """星評価・投稿日の候補をクラス名・テキストパターンで収集する。"""
  stars: list[dict[str, str]] = []
  dates: list[str] = []
  try:
    for el in page.query_selector_all(
      '[class*="star"], [class*="rating"], [class*="score"]'
    )[:30]:
      try:
        cls = el.get_attribute("class") or ""
        txt = (el.inner_text() or "").strip()
        stars.append(
          {"tag": el.evaluate("el => el.tagName") or "", "class": cls, "text": txt[:40]}
        )
      except Exception:  # noqa: BLE001, S112
        continue
  except Exception:  # noqa: BLE001, S110
    pass
  try:
    for el in page.query_selector_all("svg, img[src*='star'], img[src*='Star']")[:30]:
      try:
        cls = el.get_attribute("class") or ""
        src = el.get_attribute("src") or ""
        stars.append(
          {
            "tag": el.evaluate("el => el.tagName") or "",
            "class": cls,
            "src": src[:60],
            "text": (el.inner_text() or "").strip()[:40],
          }
        )
      except Exception:  # noqa: BLE001, S112
        continue
  except Exception:  # noqa: BLE001, S110
    pass
  text = body_text(page)
  for m in DATE_RE.finditer(text):
    dates.append(m.group(0))
  return {"stars": stars[:20], "dates": dates[:20]}


def inspect_once(url: str, timeout: int) -> dict[str, Any]:
  """1 回のブラウザコンテキストで調査し、結果を辞書で返す。"""
  result: dict[str, Any] = {"url": url}
  captured: dict[str, Any] = {"requests": [], "responses": []}
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

        def on_request(req) -> None:
          try:
            u = req.url
          except Exception:  # noqa: BLE001
            return
          entry: dict[str, Any] = {
            "url": u,
            "method": req.method,
            "resource_type": req.resource_type,
            "graphql": None,
          }
          low = u.lower()
          if req.method == "POST" and "graphql" in low:
            try:
              body = req.post_data()
              if not body:
                try:
                  buf = req.post_data_buffer()
                  if buf:
                    body = buf.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                  body = None
              if body:
                entry["graphql"] = body[:2000]
            except Exception:  # noqa: BLE001, S110
              pass
          if u not in [r["url"] for r in captured["requests"]]:
            captured["requests"].append(entry)

        def on_response(resp) -> None:
          try:
            u = resp.url
          except Exception:  # noqa: BLE001
            return
          entry: dict[str, Any] = {"url": u, "status": resp.status, "json_keys": None}
          is_json = (
            "application/json" in (resp.headers.get("content-type") or "").lower()
          )
          if is_json:
            try:
              data = resp.json()
              if isinstance(data, dict):
                entry["json_keys"] = list(data.keys())[:30]
            except Exception:  # noqa: BLE001, S110
              pass
          captured["responses"].append(entry)

        page.on("request", on_request)
        page.on("response", on_response)

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

        gate = detect_age_gate(page)
        result["age_gate_initial"] = gate

        age_dump: dict[str, Any] | None = None
        bypass: dict[str, Any] | None = None
        if gate["detected"]:
          age_dump = dump_age_check_page(page)
          result["age_dump"] = age_dump
          bypass = try_bypass_age_gate(page)
          result["age_bypass"] = bypass
          if not is_real_age_gate(page):
            bypass["cookies"] = context.cookies()

        # 動的レビュー読み込み調査
        scroll = scroll_to_review(page)
        result["scroll"] = scroll
        more_clicked = click_more_buttons(page)
        result["more_clicked"] = more_clicked
        page.wait_for_timeout(PAGE_WAIT_MS)

        result["review_count"] = find_review_count(page)
        result["review_section"] = investigate_review_section(page)
        result["individual_reviews"] = find_individual_reviews(page)
        result["review_elements"] = discover_review_elements(page)
        result["review_links"] = find_review_links(page)
        result["star_date"] = discover_star_and_date(page)
        result["network"] = captured

      except Exception as e:  # noqa: BLE001 - 失敗は1行で原因報告
        result["error"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
      result["error"] = f"ブラウザ起動失敗 -> {type(e).__name__}: {e}"
    finally:
      if context is not None:
        context.close()
  return result


def verify_cookie_bypass(url: str, timeout: int, cookies: list[Any]) -> dict[str, Any]:
  """context.add_cookies で年齢確認を回避できるか検証する。"""
  result: dict[str, Any] = {"bypassed": None}
  with sync_playwright() as p:
    context = None
    try:
      context = p.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_DATA_DIR),
        headless=True,
        user_agent=DEFAULT_USER_AGENT,
      )
      try:
        context.add_cookies(cookies)
      except Exception as e:  # noqa: BLE001
        result["add_cookies_error"] = f"{type(e).__name__}: {e}"
        return result
      page = context.new_page()
      try:
        response = page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        result["status"] = response.status if response else None
        result["final_url"] = page.url
        page.wait_for_timeout(PAGE_WAIT_MS)
        gate = detect_age_gate(page)
        result["gate_after_cookie"] = gate
        result["bypassed"] = not gate["detected"]
      except Exception as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
      result["error"] = f"ブラウザ起動失敗 -> {type(e).__name__}: {e}"
    finally:
      if context is not None:
        context.close()
  return result


def main() -> int:
  parser = argparse.ArgumentParser(
    description="FANZA 作品ページの年齢確認・レビューDOMを調査する（読み取り専用）"
  )
  parser.add_argument("--url", required=True, help="対象URL")
  parser.add_argument(
    "--timeout", type=int, default=GOTO_TIMEOUT_MS, help="goto タイムアウト(ms)"
  )
  args = parser.parse_args()

  result = inspect_once(args.url, args.timeout)

  if "error" in result:
    print(f"Error: {result['error']}")
    return 1

  print(f"URL: {result['url']}")
  print(f"Status: {result.get('status')}")
  print(f"Final URL: {result.get('final_url')}")
  print(f"Title: {result.get('title')}")

  gate = result.get("age_gate_initial") or {}
  print(
    f"年齢確認(初回): detected={gate.get('detected')} url={gate.get('url')} title={gate.get('title')}"
  )

  ad = result.get("age_dump") or {}
  if ad:
    print("年齢確認ページ構造:")
    for inp in ad.get("inputs") or []:
      print(
        "  input type="
        + repr(inp["type"])
        + " name="
        + repr(inp["name"])
        + " value="
        + repr(inp["value"])
        + " text="
        + repr(inp["text"])
      )
    for btn in ad.get("buttons") or []:
      print(
        "  button type="
        + repr(btn["type"])
        + " name="
        + repr(btn["name"])
        + " value="
        + repr(btn["value"])
        + " text="
        + repr(btn["text"])
      )
    for ln in ad.get("links") or []:
      print("  a href=" + repr(ln["href"]) + " text=" + repr(ln["text"]))

  bypass = result.get("age_bypass")
  if bypass:
    print(
      f"年齢確認突破: method={bypass.get('method')} selector={bypass.get('selector')} "
      f"clicked_commit={bypass.get('clicked_commit')} commit_selector={bypass.get('commit_selector')}"
    )
    cookies = bypass.get("cookies")
    if cookies:
      print("突破後に設定されたCookie (加齢確認関連候補):")
      for c in cookies:
        name = c.get("name", "")
        if any(
          k in name.lower() for k in ("age", "18", "adult", "dmm", "fanza", "check")
        ):
          print(
            f"  name={name} value={c.get('value')} domain={c.get('domain')} "
            f"path={c.get('path')}"
          )
      verify = verify_cookie_bypass(args.url, args.timeout, cookies)
      print(
        f"add_cookies 回避検証: bypassed={verify.get('bypassed')} "
        f"status={verify.get('status')} final_url={verify.get('final_url')} "
        f"error={verify.get('error')}"
      )
      if verify.get("gate_after_cookie"):
        gc = verify["gate_after_cookie"]
        print(
          f"  cookie後ゲート: detected={gc.get('detected')} url={gc.get('url')} "
          f"title={gc.get('title')}"
        )

  rc = result.get("review_count")
  print(f"レビュー件数: {rc}")

  scroll = result.get("scroll") or {}
  print(
    f"スクロール: scrolled={scroll.get('scrolled')} review_visible={scroll.get('review_visible')}"
  )
  print(f"もっと見るクリック: {result.get('more_clicked')}")

  rs = result.get("review_section") or {}
  if rs.get("section"):
    s = rs["section"]
    print(
      "レビューセクション: <"
      + s["tag"]
      + "> id="
      + repr(s["id"])
      + " class="
      + repr(s["class"])
    )
    print("レビューセクション本文(先頭2500): " + repr(rs.get("section_text", "")))
    print("個別レビュー候補 (最大20):")
    for r in rs.get("reviews") or []:
      print(
        "  - <div> id="
        + repr(r["id"])
        + " class="
        + repr(r["class"])
        + " text="
        + repr(r["text"])
        + " links="
        + repr(r["links"])
      )
  ir = result.get("individual_reviews") or []
  if ir:
    print("日付推定の個別レビュー (最大20):")
    for r in ir:
      print(
        "  - class="
        + repr(r["class"])
        + " date="
        + repr(r["date"])
        + " stars="
        + repr(r["stars"])
        + " body="
        + repr(r["body"])
        + " links="
        + repr(r["links"])
      )

  print("レビュー関連要素 (class/id*review, 最大20):")
  for el in result.get("review_elements") or []:
    print(
      "  - <"
      + el["tag"]
      + "> id="
      + repr(el["id"])
      + " class="
      + repr(el["class"])
      + " text="
      + repr(el["text"])
    )

  print("レビュー関連リンク (href*review, 最大20):")
  for l in result.get("review_links") or []:
    print("  - " + l["href"] + " | " + repr(l["text"]))

  net = result.get("network") or {}
  print("ネットワークリクエスト (レビュー/AJAX/API 関連, 最大30):")
  count = 0
  for req in net.get("requests") or []:
    low = req["url"].lower()
    if any(k in low for k in ("review", "ajax", "api", "comment")):
      print(
        "  - " + req["method"] + " " + req["url"] + " [" + req["resource_type"] + "]"
      )
      if req.get("graphql"):
        print("    graphql_body=" + repr(req["graphql"]))
      count += 1
      if count >= 30:
        break
  print("ネットワークレスポンス (JSON, 最大30):")
  count = 0
  for resp in net.get("responses") or []:
    if resp.get("json_keys") is not None:
      low = resp["url"].lower()
      if any(k in low for k in ("review", "ajax", "api", "comment")):
        print(
          "  - "
          + str(resp["status"])
          + " "
          + resp["url"]
          + " keys="
          + repr(resp["json_keys"])
        )
        count += 1
        if count >= 30:
          break

  sd = result.get("star_date") or {}
  print("星評価候補 (class*star/rating/score, svg, img*star, 最大20):")
  for s in sd.get("stars") or []:
    print(
      "  - <"
      + s["tag"]
      + "> class="
      + repr(s["class"])
      + " src="
      + repr(s.get("src", ""))
      + " text="
      + repr(s["text"])
    )
  print("投稿日候補 (最大20):")
  for d in sd.get("dates") or []:
    print("  - " + d)

  return 0


if __name__ == "__main__":
  sys.exit(main())
