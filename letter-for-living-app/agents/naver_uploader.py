import random
import re
import time
from pathlib import Path
import shutil

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def open_naver_writer(
    write_url: str,
    naver_id: str,
    naver_password: str,
    title: str,
    body: str,
    profile_dir: str,
    project_root: Path,
    image_paths: list[str] | None = None,
) -> None:
    driver_path = shutil.which("chromedriver")
    if not driver_path:
        raise RuntimeError("chromedriver를 찾을 수 없습니다. 설치 후 다시 시도해 주세요.")

    options = webdriver.ChromeOptions()
    options.add_experimental_option("detach", True)
    chrome_candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    chrome_bin = next((p for p in chrome_candidates if Path(p).exists()), "")
    if chrome_bin:
        options.binary_location = chrome_bin
    else:
        raise RuntimeError("Chrome 브라우저를 찾을 수 없습니다. Chrome 설치 후 다시 시도해 주세요.")

    if profile_dir:
        Path(profile_dir).mkdir(parents=True, exist_ok=True)
        lock_path = Path(profile_dir) / "SingletonLock"
        if lock_path.exists():
            raise RuntimeError(
                "Chrome 프로필이 다른 창에서 사용 중입니다. "
                "모든 Chrome 창을 닫고 다시 시도해 주세요."
            )
        options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")

    log_path = project_root / "logs" / "chromedriver.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    service = Service(driver_path, log_output=str(log_path))
    driver = webdriver.Chrome(service=service, options=options)
    driver.get(write_url)

    wait = WebDriverWait(driver, 15)

    if "nid.naver.com" in driver.current_url:
        WebDriverWait(driver, 180).until(lambda d: "nid.naver.com" not in d.current_url)

    driver.get(write_url)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    def find_first(selectors: list[str]):
        for selector in selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                return elements[0]
        return None

    def locate_in_frames(selectors: list[str], timeout: int = 40):
        end = time.time() + timeout
        while time.time() < end:
            frames = [None]
            try:
                frames.extend(driver.find_elements(By.TAG_NAME, "iframe"))
            except Exception:
                pass
            for frame in frames:
                try:
                    if frame is None:
                        driver.switch_to.default_content()
                    else:
                        driver.switch_to.frame(frame)
                except Exception:
                    continue
                el = find_first(selectors)
                if el:
                    return el
            time.sleep(0.5)
        return None

    def click_in_frames(selectors: list[str], timeout: int = 20) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            frames = [None]
            try:
                frames.extend(driver.find_elements(By.TAG_NAME, "iframe"))
            except Exception:
                pass
            for frame in frames:
                try:
                    if frame is None:
                        driver.switch_to.default_content()
                    else:
                        driver.switch_to.frame(frame)
                except Exception:
                    continue
                for selector in selectors:
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, selector)
                        el.click()
                        return True
                    except Exception:
                        continue
            time.sleep(0.5)
        return False

    def insert_image(path: str) -> None:
        image_button_selectors = [
            "button.se-image-toolbar-button",
            "button[data-name='image']",
            "button.se-toolbar-btn-image",
            "button[title*='사진']",
            "button[title*='이미지']",
            "button[aria-label*='이미지']",
        ]
        click_in_frames(image_button_selectors, timeout=10)
        time.sleep(0.6)
        file_input = locate_in_frames(
            [
                "input#hidden-file",
                "input[type='file'][accept*='image']",
                "input[type='file']",
            ],
            timeout=20,
        )
        if file_input:
            try:
                file_input.send_keys(path)
                time.sleep(2.5)
            except Exception:
                try:
                    driver.execute_script(
                        "const inputs = document.querySelectorAll('input#hidden-file');"
                        "const el = inputs[inputs.length - 1];"
                        "if(el){el.value='';}",
                    )
                    file_input.send_keys(path)
                    time.sleep(2.5)
                except Exception:
                    pass

    def compute_image_positions(paragraph_count: int, image_count: int) -> list[int]:
        if paragraph_count <= 0 or image_count <= 0:
            return []
        base_positions = [0, 2, 3, 4]
        positions: list[int] = []
        for idx in range(min(image_count, len(base_positions))):
            pos = base_positions[idx]
            if pos >= paragraph_count:
                pos = paragraph_count - 1
            while positions and pos <= positions[-1] and pos < paragraph_count - 1:
                pos += 1
            positions.append(pos)
        return positions

    def click_align_button(mode: str) -> None:
        if mode == "center":
            selectors = [
                "button[data-name='alignCenter']",
                "button[title*='가운데']",
                "button[aria-label*='가운데']",
            ]
        else:
            selectors = [
                "button[data-name='alignLeft']",
                "button[title*='왼쪽']",
                "button[aria-label*='왼쪽']",
            ]
        click_in_frames(selectors, timeout=4)

    def set_by_placeholder(match_text: str, mode: str, text_value: str) -> bool:
        frames = [None]
        try:
            frames.extend(driver.find_elements(By.TAG_NAME, "iframe"))
        except Exception:
            pass
        for frame in frames:
            try:
                if frame is None:
                    driver.switch_to.default_content()
                else:
                    driver.switch_to.frame(frame)
            except Exception:
                continue
            try:
                found = driver.execute_script(
                    """
                    const mode = arguments[0];
                    const key = arguments[1];
                    const placeholders = Array.from(document.querySelectorAll('span.se-placeholder'));
                    const match = placeholders.find(p => {
                      const txt = (p.textContent || '').trim();
                      if (!txt) return false;
                      if (mode === 'exact') return txt === key;
                      return txt.includes(key);
                    });
                    if (!match) return false;
                    match.click();
                    return true;
                    """,
                    mode,
                    match_text,
                )
                if found:
                    try:
                        active = driver.switch_to.active_element
                        set_element_text(active, text_value)
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def set_element_text(el, text: str) -> bool:
        try:
            el.click()
        except Exception:
            pass
        try:
            def human_type(target, value: str) -> None:
                for ch in value:
                    target.send_keys(ch)
                    if ch in ".!?\n":
                        time.sleep(random.uniform(0.15, 0.4))
                    else:
                        time.sleep(random.uniform(0.02, 0.06))

            try:
                active = driver.switch_to.active_element
                for ch in text:
                    ActionChains(driver).send_keys(ch).perform()
                    if ch in ".!?\n":
                        time.sleep(random.uniform(0.15, 0.4))
                    else:
                        time.sleep(random.uniform(0.02, 0.06))
            except Exception:
                human_type(el, text)
            return True
        except Exception:
            return False

    def is_title_element(el) -> bool:
        try:
            return bool(
                driver.execute_script(
                    "return !!arguments[0].closest('div.se-title-text, textarea.se-title-input, input.se-title-input, input#title');",
                    el,
                )
            )
        except Exception:
            return False

    title_el = None
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    placeholder_el = locate_in_frames(["span.se-placeholder"], timeout=10)
    if placeholder_el:
        try:
            text = (placeholder_el.text or "").strip()
            if text == "제목":
                title_el = placeholder_el
        except Exception:
            title_el = None
    if not title_el:
        title_selectors = [
            "textarea.se-title-input",
            "input#title",
            "input.se-title-input",
            "div.se-title-text",
        ]
        title_el = locate_in_frames(title_selectors, timeout=20)
    if title_el:
        set_element_text(title_el, title)
    else:
        set_by_placeholder("제목", "exact", title)

    body_selectors = [
        "p.se-text-paragraph .se-placeholder",
        "p.se-text-paragraph.se-placeholder-focused",
        "p.se-text-paragraph",
        "div.se-component-content",
        "div.se-text-paragraph",
        "div[contenteditable='true']",
        "textarea#content",
        "textarea[name='content']",
    ]
    body_el = locate_in_frames(body_selectors, timeout=60)
    if body_el and is_title_element(body_el):
        body_el = None
    if body_el:
        time.sleep(0.6)
        normalized = body.replace("\r\n", "\n").strip()
        paragraphs = [p for p in re.split(r"\n\s*\n", normalized) if p.strip()]
        cleaned_image_paths = [
            path for path in (image_paths or []) if path and Path(path).exists()
        ]
        image_positions = compute_image_positions(
            len(paragraphs), len(cleaned_image_paths)
        )
        image_map = {
            pos: cleaned_image_paths[idx]
            for idx, pos in enumerate(image_positions)
        }
        if paragraphs:
            try:
                body_el.click()
            except Exception:
                pass
            click_align_button("center")
            if 0 in image_map:
                insert_image(image_map[0])
            set_element_text(body_el, paragraphs[0])
            try:
                body_el.send_keys("\n\n")
            except Exception:
                pass
            click_align_button("left")
            for idx, para in enumerate(paragraphs[1:], start=1):
                try:
                    body_el.send_keys("\n\n")
                except Exception:
                    pass
                if idx in image_map:
                    insert_image(image_map[idx])
                set_element_text(body_el, para)
        else:
            if not set_element_text(body_el, body):
                if not set_by_placeholder("일상을", "contains", body):
                    set_by_placeholder("글감과 함께", "contains", body)
    else:
        if not set_by_placeholder("일상을", "contains", body):
            set_by_placeholder("글감과 함께", "contains", body)

    if image_paths and not body:
        cleaned_image_paths = [
            path for path in image_paths if path and Path(path).exists()
        ]
        if cleaned_image_paths:
            insert_image(cleaned_image_paths[0])
