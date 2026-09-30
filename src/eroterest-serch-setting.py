import os
import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", ".browsers")


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
      # モーダル/設定フォームエリアの表示を明示的に待機
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

      # .form-group.proids と各チェックボックスの display: none を強制的に表示状態に
      page.evaluate("""() => {
        document.querySelectorAll('.form-group.proids, .form-group.proids *').forEach(el => {
          if (el.style.display === 'none') el.style.display = 'block';
        });
        document.querySelectorAll('input[type="checkbox"]').forEach(el => {
          if (el.style.display === 'none') el.style.display = 'block';
        });
      }""")
      print("Forced display for .form-group.proids and checkboxes")

      # 未チェックにしたいIDのチェックを外す
      unchecked_ids = ["31", "33", "38", "39", "46", "49", "50"]

      # すべてにチェックを入れる/外すリンクをクリックして全選択状態に
      all_check_link = page.locator(".mypageSettingProidAllCheck")
      # まずリンクをクリックして全選択状態にする（リンクの動作に依存）
      all_check_link.click(timeout=30000)
      print("Clicked all check link (select all)")

      # 未チェックにしたいIDのチェックを外す（evaluate で直接変更）
      for id_val in unchecked_ids:
        page.evaluate(
          """(id) => {{
            const cb = document.getElementById('mypage_setting_proids_' + id);
            if (cb) {{
              cb.style.display = 'block';
              cb.checked = false;
            }}
          }}""",
          id_val,
        )
      print("Unchecked IDs: 31, 33, 38, 39, 46, 49, 50")

      # 再生時間: 20分以上（evaluate で直接変更）
      page.evaluate("""() => {
        const cb = document.getElementById('mypage_setting_times_20');
        if (cb) { cb.style.display = 'block'; cb.checked = true; }
      }""")
      print("Selected time: 20")

      # 表示件数: 40件（evaluate で直接変更）
      page.evaluate("""() => {
        const cb = document.getElementById('mypage_setting_perpage_40');
        if (cb) { cb.style.display = 'block'; cb.checked = true; }
      }""")
      print("Selected perpage: 40")

      # 動画削除済み記事: 表示しない（evaluate で直接変更）
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

      # 保存完了後のスクリーンショット
      page.wait_for_load_state("networkidle", timeout=30000)
      page.screenshot(
        path="screenshot/eroterest_search_setting_saved.png", full_page=True
      )
      print("Saved screenshot saved")

      page.screenshot(path="screenshot/eroterest_search_setting.png", full_page=True)
      print("Screenshot saved to screenshot/eroterest_search_setting.png")
    except PlaywrightError as e:
      print(f"Failed to open page: {e}")
      page.screenshot(
        path="screenshot/error_eroterest_search_setting.png", full_page=True
      )
      print("Error screenshot saved to screenshot/error_eroterest_search_setting.png")
    finally:
      context.close()


if __name__ == "__main__":
  main()
