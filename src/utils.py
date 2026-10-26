import json
import os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SCREENSHOT_DIR = PROJECT_ROOT / "screenshot"
DATA_PATH = DATA_DIR / "eroterest-page.json"

DEFAULT_USER_AGENT = (
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


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
    os.chmod(tmp_path, path.stat().st_mode & 0o777 if path.exists() else 0o664)
    os.replace(tmp_path, path)
  except BaseException:
    tmp_path.unlink(missing_ok=True)
    raise


def save_screenshot(page, path: Path) -> None:
  """スクリーンショットを保存する。失敗しても元のエラーを上書きしない。"""
  try:
    page.screenshot(path=str(path), full_page=True)
  except Exception as e:  # noqa: BLE001 - 元のエラーを隠さないため意図的に広域に捕捉
    print(f"Warning: スクリーンショットを保存できませんでした ({e})")
    return
  print(f"Screenshot saved to {path}")
