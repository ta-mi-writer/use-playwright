import os
import re
import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", ".browsers")

UNCHECKED_IDS = ["31", "33", "38", "39", "46", "49", "50"]


def main():
  with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
      user_data_dir=".browser-data",
      headless=True,
      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    page = context.new_page()
    try:
      response = page.goto(
        "https://movie.eroterest.net/?word=%E7%86%9F%E5%A5%B3&c=&page=1",
        timeout=30000,
        wait_until="domcontentloaded",
      )
      print(f"Status: {response.status if response else 'No response'}")
      print(f"URL: {page.url}")
      page.wait_for_load_state("networkidle", timeout=30000)
      link = page.get_by_role("link", name=re.compile("表示設定"))
      link.click(timeout=30000)
      print("Clicked '表示設定' link")
      settings_form = page.locator(
        "#mypageSettingForm, .mypageSettingForm, .modal, [role='dialog']"
      ).first
      settings_form.wait_for(state="visible", timeout=30000)
      print("Settings form/modal is visible")
      settings_heading = page.get_by_role(
        "heading",
        name=re.compile("表示設定"),
      )
      settings_heading.wait_for(state="visible", timeout=30000)
      print("Settings heading is visible")

      page.evaluate("""() => {
        document.querySelectorAll('.form-group.proids, .form-group.proids *').forEach(el => {
          if (el.style.display === 'none') el.style.display = 'block';
        });
        document.querySelectorAll('input[type="checkbox"]').forEach(el => {
          if (el.style.display === 'none') el.style.display = 'block';
        });
      }""")
      print("Forced display for .form-group.proids and checkboxes")

      # 決定的に設定: 全プロイドをチェックし、対象7つだけ未チェックに
      page.evaluate(
        """(ids) => {
          document.querySelectorAll('input[name="proids[]"]').forEach(cb => {
            cb.checked = true;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
          });
          ids.forEach(id => {
            const cb = document.getElementById('mypage_setting_proids_' + id);
            if (cb) {
              cb.checked = false;
              cb.dispatchEvent(new Event('change', { bubbles: true }));
            }
          });
        }""",
        UNCHECKED_IDS,
      )
      print(f"Set all proids checked, unchecked: {UNCHECKED_IDS}")

      # 再生時間: 20分以上
      page.evaluate("""() => {
        const cb = document.getElementById('mypage_setting_times_20');
        if (cb) { cb.style.display = 'block'; cb.checked = true; }
      }""")
      print("Selected time: 20")

      # 表示件数: 40件
      page.evaluate("""() => {
        const cb = document.getElementById('mypage_setting_perpage_40');
        if (cb) { cb.style.display = 'block'; cb.checked = true; }
      }""")
      print("Selected perpage: 40")

      # 動画削除済み記事: 表示しない
      page.evaluate("""() => {
        const cb = document.getElementById('mypage_setting_hide_deleted_1');
        if (cb) { cb.style.display = 'block'; cb.checked = true; }
      }""")
      print("Selected hide_deleted: 1")

      # スクロールしながら複数枚スクリーンショット撮影
      page.screenshot(
        path="screenshot/eroterest_search_setting_configured_1.png", full_page=True
      )
      print("Screenshot 1 saved")
      page.evaluate("window.scrollBy(0, 300)")
      page.screenshot(
        path="screenshot/eroterest_search_setting_configured_2.png", full_page=True
      )
      print("Screenshot 2 saved")
      page.evaluate("window.scrollBy(0, 300)")
      page.screenshot(
        path="screenshot/eroterest_search_setting_configured_3.png", full_page=True
      )
      print("Screenshot 3 saved")

      # 設定を保存
      save_button = page.locator("input[value='設定を保存']")
      save_button.click(timeout=30000)
      print("Clicked save button")

      # 保存完了後のスクリーンショット（networkidle は使わない）
      page.wait_for_timeout(1500)
      page.screenshot(
        path="screenshot/eroterest_search_setting_saved.png", full_page=True
      )
      print("Saved screenshot saved")

      page.screenshot(path="screenshot/eroterest_search_setting.png", full_page=True)
      print("Screenshot saved to screenshot/eroterest_search_setting.png")

      # ---- 自己検証: 設定画面を再度開きチェック状態を集計 ----
      link = page.get_by_role("link", name=re.compile("表示設定"))
      link.click(timeout=30000)
      print("Re-opened settings for verification")
      settings_form.wait_for(state="visible", timeout=30000)
      page.evaluate("""() => {
        document.querySelectorAll('.form-group.proids, .form-group.proids *').forEach(el => {
          if (el.style.display === 'none') el.style.display = 'block';
        });
        document.querySelectorAll('input[type="checkbox"]').forEach(el => {
          if (el.style.display === 'none') el.style.display = 'block';
        });
      }""")

      result = page.evaluate(
        """(ids) => {
          const boxes = document.querySelectorAll('input[name="proids[]"]');
          let checked = 0;
          let unchecked = 0;
          const uncheckedIds = [];
          boxes.forEach(cb => {
            if (cb.checked) checked += 1;
            else { unchecked += 1; uncheckedIds.push(cb.id); }
          });
          const targetUnchecked = ids.every(id =>
            document.getElementById('mypage_setting_proids_' + id) &&
            !document.getElementById('mypage_setting_proids_' + id).checked
          );
          return { total: boxes.length, checked, unchecked, uncheckedIds, targetUnchecked };
        }""",
        UNCHECKED_IDS,
      )
      print(
        f"Verification: total={result['total']} checked={result['checked']} "
        f"unchecked={result['unchecked']} targetUnchecked={result['targetUnchecked']}"
      )
      print(f"Unchecked IDs: {result['uncheckedIds']}")

      ok = (
        result["unchecked"] == 7
        and result["checked"] == result["total"] - 7
        and result["targetUnchecked"]
      )
      if not ok:
        print("VERIFICATION FAILED: settings do not match expected state")
        sys.exit(1)
      print("VERIFICATION PASSED: 34 checked / 7 unchecked, all target IDs unchecked")

    except PlaywrightError as e:
      print(f"Failed to open page: {e}")
      page.screenshot(
        path="screenshot/error_eroterest_search_setting.png", full_page=True
      )
      print("Error screenshot saved to screenshot/error_eroterest_search_setting.png")
      sys.exit(1)
    finally:
      context.close()


if __name__ == "__main__":
  main()
