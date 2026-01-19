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
from selenium.webdriver.common.keys import Keys
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
    def log_step(message: str) -> None:
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "naver-uploader-debug.log"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    def capture_debug(name: str) -> None:
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)
        path = log_dir / f"naver-uploader-{safe_name}.png"
        try:
            driver.save_screenshot(str(path))
        except Exception:
            pass

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
    log_step("webdriver started")
    driver.get(write_url)
    log_step(f"open write url: {write_url}")

    wait = WebDriverWait(driver, 15)

    if "nid.naver.com" in driver.current_url:
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            log_step("login page detected, waiting for manual login")
            print("네이버 로그인 페이지입니다. 브라우저에서 수동으로 로그인해 주세요.")
        except Exception:
            pass
        WebDriverWait(driver, 300).until(lambda d: "nid.naver.com" not in d.current_url)
        log_step("login completed")

    driver.get(write_url)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    log_step("writer page loaded")
    capture_debug("writer-loaded")

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

    def ensure_editor_ready(timeout: int = 40) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            except Exception:
                pass
            title_probe = locate_in_frames(
                [
                    "textarea.se-title-input",
                    "input#title",
                    "input.se-title-input",
                    "div.se-title-text",
                    "span.se-placeholder",
                ],
                timeout=2,
            )
            if title_probe:
                return
            time.sleep(0.6)

    ensure_editor_ready()
    log_step("editor ready")

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

    def insert_quote_block(quote_text: str, source_text: str) -> bool:
        quote_button_selectors = [
            "button.se-quote-toolbar-button",
            "button[data-name='quote']",
            "button[data-name='quotation']",
            "button.se-toolbar-btn-quote",
            "button.se-insert-menu-sub-panel-button-quotation-default",
            "button[title*='인용구']",
            "button[aria-label*='인용구']",
        ]
        if not click_in_frames(quote_button_selectors, timeout=8):
            return False
        time.sleep(0.6)

        quote_selectors = [
            "div.se-quotation [contenteditable='true']",
            "div.se-quote [contenteditable='true']",
            "div.se-component-quote [contenteditable='true']",
            "div.se-quotation blockquote",
            "div.se-quote blockquote",
            "blockquote",
        ]
        source_selectors = [
            "input.se-quotation-source",
            "input.se-quote-source",
            "input.se-quotation-source-input",
            "input.se-quote-source-input",
            "input.se-quotation-source-text",
            "input.se-quote-source-text",
            "input[placeholder*='출처']",
            "input[aria-label*='출처']",
            "input[title*='출처']",
            "div.se-quotation-source [contenteditable='true']",
        ]

        quote_el = locate_in_frames(quote_selectors, timeout=10)
        if not quote_el:
            try:
                quote_el = driver.execute_script(
                    """
                    const blocks = Array.from(document.querySelectorAll(
                      'div.se-quotation, div.se-quote, div.se-component-quote'
                    ));
                    const block = blocks[blocks.length - 1];
                    if (!block) return null;
                    return block.querySelector('[contenteditable=\"true\"], blockquote, p');
                    """
                )
            except Exception:
                quote_el = None
        if quote_el:
            set_element_text(quote_el, quote_text)
        else:
            return False

        if source_text:
            source_el = locate_in_frames(source_selectors, timeout=6)
            if source_el:
                set_element_text(source_el, source_text)
            else:
                try:
                    source_el = driver.execute_script(
                        """
                        const blocks = Array.from(document.querySelectorAll(
                          'div.se-quotation, div.se-quote, div.se-component-quote'
                        ));
                        const block = blocks[blocks.length - 1];
                        if (!block) return null;
                        const candidates = Array.from(block.querySelectorAll(
                          'input, textarea, [contenteditable=\"true\"]'
                        ));
                        const match = candidates.find(el => {
                          const placeholder = (el.getAttribute('placeholder') || '').trim();
                          const aria = (el.getAttribute('aria-label') || '').trim();
                          const title = (el.getAttribute('title') || '').trim();
                          const data = (el.getAttribute('data-placeholder') || '').trim();
                          const text = [placeholder, aria, title, data].join(' ');
                          if (text.includes('출처')) return true;
                          return !!el.closest('.se-quotation-source, .se-quote-source');
                        });
                        return match || null;
                        """
                    )
                    if source_el:
                        set_element_text(source_el, source_text)
                except Exception:
                    set_by_placeholder("출처", "contains", source_text)
        return True

    def insert_horizontal_line() -> bool:
        line_button_selectors = [
            "button.se-toolbar-option-insert-horizontal-line-line1-button",
            "button[data-name='horizontal-line'][data-value='line1']",
            "button[data-name='horizontal-line']",
            "button[title*='구분선']",
            "button[aria-label*='구분선']",
        ]
        return click_in_frames(line_button_selectors, timeout=6)

    def open_text_format_menu() -> bool:
        format_button_selectors = [
            "button[data-name='text-format']",
            "button.se-text-format-toolbar-button",
            "button.se-property-toolbar-label-select-button",
        ]
        return click_in_frames(format_button_selectors, timeout=6)

    def select_text_format(label: str, fallback_selector: str | None = None) -> bool:
        if not open_text_format_menu():
            return False
        time.sleep(0.4)
        try:
            found = driver.execute_script(
                """
                const fallback = arguments[0];
                const label = arguments[1];
                if (fallback) {
                  const direct = document.querySelector(fallback);
                  if (direct) {
                    direct.click();
                    return true;
                  }
                }
                const labels = Array.from(document.querySelectorAll('span.se-toolbar-option-label'));
                const target = labels.find(node => (node.textContent || '').trim() === label);
                if (!target) return false;
                const button = target.closest('button');
                if (!button) return false;
                button.click();
                return true;
                """,
                fallback_selector,
                label,
            )
            return bool(found)
        except Exception:
            return False

    def insert_subheading(target_el, text: str) -> bool:
        try:
            target_el.click()
        except Exception:
            pass
        if not select_text_format(
            "소제목",
            "button.se-toolbar-option-text-format-sectionTitle-button",
        ):
            return False
        time.sleep(0.6)
        try:
            if not set_element_text(target_el, text):
                return False
        except Exception:
            return False
        try:
            ActionChains(driver).send_keys(Keys.ENTER).pause(0.2).send_keys(Keys.ENTER).perform()
        except Exception:
            pass
        time.sleep(0.4)
        select_text_format("본문", "button.se-toolbar-option-text-format-body-button")
        time.sleep(0.3)
        return True

    def parse_section_heading(text: str) -> str | None:
        normalized = re.sub(r"^\s*#+\s*", "", text.strip())
        match = re.match(
            r"^(배경|의미|묵상|체크리스트|되짚어볼 질문|요약)\s*[:：]?$",
            normalized,
        )
        return match.group(1) if match else None

    def split_section_paragraphs(paragraphs: list[str]) -> list[str]:
        processed: list[str] = []
        for para in paragraphs:
            heading_pattern = r"#+\s*(배경|의미|묵상|체크리스트|되짚어볼 질문|요약)\s*"
            para = re.sub(heading_pattern, r"\n\1\n", para)
            lines = [line.strip() for line in para.splitlines() if line.strip()]
            if not lines:
                continue
            buffer: list[str] = []
            for line in lines:
                heading = parse_section_heading(line)
                if heading:
                    if buffer:
                        processed.append("\n".join(buffer).strip())
                        buffer = []
                    processed.append(heading)
                    continue
                inline_match = re.match(
                    r"^(배경|의미|묵상|체크리스트|되짚어볼 질문|요약)\s*[:：]\s*(.+)$",
                    line,
                )
                if inline_match:
                    if buffer:
                        processed.append("\n".join(buffer).strip())
                        buffer = []
                    processed.append(inline_match.group(1))
                    processed.append(inline_match.group(2).strip())
                    continue
                buffer.append(line)
            if buffer:
                processed.append("\n".join(buffer).strip())
        return processed

    def normalize_checklist_text(text: str) -> str:
        lines = text.splitlines()
        cleaned = []
        for line in lines:
            stripped = line.lstrip()
            stripped = re.sub(r"^[-•]+\s*", "", stripped)
            if stripped:
                cleaned.append(f"• {stripped}")
        return "\n".join(cleaned).strip()

    def normalize_bullets(text: str) -> str:
        lines = text.splitlines()
        cleaned = []
        for line in lines:
            stripped = line.lstrip()
            stripped = re.sub(r"^[-•]+\s*", "", stripped)
            if stripped:
                cleaned.append(f"• {stripped}")
        return "\n".join(cleaned).strip()

    def normalize_qa_text(text: str) -> str:
        cleaned = text.replace("\r\n", "\n").strip()
        matches = list(re.finditer(r"(Q\.|A\.)", cleaned))
        if not matches:
            return cleaned
        output: list[str] = []
        for idx, match in enumerate(matches):
            marker = match.group(1)
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(cleaned)
            content = cleaned[start:end].strip()
            content = " ".join(content.split())
            if marker == "Q." and output:
                output.append("")
            if content:
                output.append(f"{marker} {content}")
            else:
                output.append(marker)
        return "\n".join(output).strip()

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

    def click_bold_button() -> bool:
        selectors = [
            "button[data-name='bold']",
            "button.se-bold-toolbar-button",
            "button.se-toolbar-btn-bold",
            "button[title*='굵게']",
            "button[aria-label*='굵게']",
        ]
        return click_in_frames(selectors, timeout=4)

    def detect_bold_active() -> bool | None:
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
                state = driver.execute_script(
                    """
                    const selectors = [
                      "button[data-name='bold']",
                      "button.se-bold-toolbar-button",
                      "button.se-toolbar-btn-bold"
                    ];
                    const btn = selectors.map(s => document.querySelector(s)).find(Boolean);
                    if (!btn) return null;
                    const pressed = btn.getAttribute('aria-pressed');
                    if (pressed !== null) return pressed === 'true';
                    return btn.classList.contains('se-toolbar-option-active')
                      || btn.classList.contains('active');
                    """
                )
                if state is not None:
                    return bool(state)
            except Exception:
                continue
        return None

    def set_bold_enabled(enabled: bool) -> None:
        state = detect_bold_active()
        if state is None:
            click_bold_button()
            return
        if state != enabled:
            click_bold_button()

    def insert_qa_block(target_el, text: str) -> None:
        lines = text.replace("\r\n", "\n").split("\n")
        first = True
        def insert_enter() -> None:
            try:
                ActionChains(driver).send_keys(Keys.ENTER).perform()
            except Exception:
                try:
                    target_el.send_keys("\n")
                except Exception:
                    pass

        for raw in lines:
            line = raw.strip()
            if not first:
                insert_enter()
            if not line:
                first = False
                continue
            if line.startswith("Q."):
                set_bold_enabled(True)
                set_element_text(target_el, line)
                set_bold_enabled(False)
            else:
                set_element_text(target_el, line)
            first = False

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
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                    el,
                )
            except Exception:
                pass
            el.click()
        except Exception:
            pass
        try:
            try:
                driver.execute_script("arguments[0].focus();", el)
            except Exception:
                pass
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
            try:
                applied = driver.execute_script(
                    """
                    const el = arguments[0];
                    const val = arguments[1];
                    if (!el) return false;
                    const tag = (el.tagName || '').toLowerCase();
                    if (tag === 'input' || tag === 'textarea') {
                      el.value = val;
                    } else if (el.isContentEditable) {
                      el.textContent = val;
                    } else {
                      el.textContent = val;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                    """,
                    el,
                    text,
                )
                return bool(applied)
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
        log_step("title set")
    else:
        set_by_placeholder("제목", "exact", title)
        log_step("title set via placeholder")

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
        log_step("body element located")
        paragraphs = [p for p in re.split(r"\n\s*\n", normalized) if p.strip()]
        hashtags_line = ""
        closing_paragraph = ""
        closing_sentence = "더 많은 묵상과 영감을 원하신다면, 저희 프로젝트를 확인해 보세요."

        def is_hashtag_candidate(text: str) -> bool:
            if "#" in text:
                return True
            if "http" in text:
                return False
            if re.search(r"[.!?]", text):
                return False
            if len(text) > 80:
                return False
            tokens = text.split()
            if len(tokens) < 2:
                return False
            return bool(re.match(r"^[\w\s가-힣]+$", text))

        def format_hashtags_line(text: str) -> str:
            if "#" in text:
                return text
            tokens = [t for t in re.split(r"\s+", text.strip()) if t]
            return " ".join(f"#{t}" for t in tokens)

        if paragraphs:
            last = paragraphs[-1].strip()
            if is_hashtag_candidate(last):
                hashtags_line = format_hashtags_line(last)
                paragraphs = paragraphs[:-1]
            if paragraphs:
                last = paragraphs[-1].strip()
                if closing_sentence in last:
                    lines = [line.strip() for line in last.splitlines() if line.strip()]
                    idx = next(
                        (i for i, line in enumerate(lines) if closing_sentence in line),
                        None,
                    )
                    if idx is not None:
                        start_idx = idx
                        if idx > 0 and "http" in lines[idx - 1]:
                            start_idx = idx - 1
                        closing_paragraph = "\n".join(lines[start_idx:]).strip()
                        summary_lines = lines[:start_idx]
                        if summary_lines:
                            paragraphs[-1] = "\n".join(summary_lines).strip()
                        else:
                            paragraphs = paragraphs[:-1]
                    else:
                        closing_paragraph = last
                        paragraphs = paragraphs[:-1]
                else:
                    closing_paragraph = last
                    paragraphs = paragraphs[:-1]
        cleaned_image_paths = [
            path for path in (image_paths or []) if path and Path(path).exists()
        ]
        if paragraphs:
            try:
                body_el.click()
            except Exception:
                pass
            click_align_button("center")
            if cleaned_image_paths:
                insert_image(cleaned_image_paths[0])

            quote_text = paragraphs[0]
            source_text = paragraphs[1] if len(paragraphs) > 1 else ""
            remaining_paragraphs = paragraphs[2:] if len(paragraphs) > 1 else []
            remaining_paragraphs = split_section_paragraphs(remaining_paragraphs)
            if not insert_quote_block(quote_text, source_text):
                set_element_text(body_el, quote_text)
                if source_text:
                    try:
                        body_el.send_keys("\n\n")
                    except Exception:
                        pass
                    set_element_text(body_el, source_text)

            try:
                body_el.send_keys("\n\n")
            except Exception:
                pass
            insert_horizontal_line()
            click_align_button("left")

            remaining_images = cleaned_image_paths[1:] if cleaned_image_paths else []
            bg_image = remaining_images[0] if remaining_images else None
            if remaining_images:
                remaining_images = remaining_images[1:]
            content_paragraphs = [
                p for p in remaining_paragraphs if not parse_section_heading(p)
            ]
            image_positions = compute_image_positions(
                len(content_paragraphs), len(remaining_images)
            )
            image_map = {
                pos: remaining_images[idx]
                for idx, pos in enumerate(image_positions)
            }
            content_idx = 0
            current_section = None
            bg_image_inserted = False
            for para in remaining_paragraphs:
                try:
                    body_el.send_keys("\n\n")
                except Exception:
                    pass
                section_label = parse_section_heading(para)
                if section_label:
                    if current_section == "배경" and bg_image and not bg_image_inserted:
                        try:
                            body_el.send_keys("\n\n")
                        except Exception:
                            pass
                        insert_image(bg_image)
                        try:
                            body_el.send_keys("\n\n")
                        except Exception:
                            pass
                        bg_image_inserted = True
                    if content_idx > 0 and not (
                        current_section == "배경" and section_label == "의미"
                    ):
                        insert_horizontal_line()
                    insert_subheading(body_el, section_label)
                    current_section = section_label
                    continue
                if content_idx in image_map:
                    insert_image(image_map[content_idx])
                if current_section == "체크리스트":
                    para = normalize_checklist_text(para)
                if current_section == "되짚어볼 질문":
                    para = normalize_qa_text(para)
                    insert_qa_block(body_el, para)
                else:
                    if current_section == "요약":
                        para = normalize_bullets(para)
                    set_element_text(body_el, para)
                content_idx += 1
            if current_section == "배경" and bg_image and not bg_image_inserted:
                try:
                    body_el.send_keys("\n\n")
                except Exception:
                    pass
                insert_image(bg_image)
                try:
                    body_el.send_keys("\n\n")
                except Exception:
                    pass
                bg_image_inserted = True
            if content_idx > 0:
                insert_horizontal_line()
            if closing_paragraph:
                try:
                    body_el.send_keys("\n\n")
                except Exception:
                    pass
                click_align_button("center")
                set_element_text(body_el, closing_paragraph)
            if hashtags_line:
                try:
                    body_el.send_keys("\n\n")
                except Exception:
                    pass
                click_align_button("center")
                set_element_text(body_el, hashtags_line)
            click_align_button("left")
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
