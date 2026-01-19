import csv
import datetime as dt
import json
import os
import re
import threading
import logging
import time
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

from agents.blog_writer import build_blog_prompt
from agents.image_generator import generate_images
from agents.naver_uploader import open_naver_writer

APP_DIR = Path(__file__).resolve().parent


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"")
        if key and key not in os.environ:
            os.environ[key] = value


load_env(APP_DIR / ".env")

DEFAULT_PROJECT_ROOT = Path("/Users/admin/Desktop/고즈넉씨스튜디오/letter-for-living")
DEFAULT_USED_VERSES = Path("/Users/admin/Desktop/고즈넉씨스튜디오/letter-for-living/used-verses.md")
DEFAULT_THEMES = Path("/Users/admin/Desktop/고즈넉씨스튜디오/letter-for-living/themes.md")

PROJECT_ROOT = Path(os.environ.get("LFL_PROJECT_ROOT", DEFAULT_PROJECT_ROOT))
USED_VERSES_PATH = Path(os.environ.get("LFL_USED_VERSES", DEFAULT_USED_VERSES))
THEMES_PATH = Path(os.environ.get("LFL_THEMES", DEFAULT_THEMES))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

BRIEFS_DIR = PROJECT_ROOT / "briefs"
LOG_PATH = PROJECT_ROOT / "logs" / "posters-log.csv"
THEME_MAP_PATH = PROJECT_ROOT / "logs" / "used-themes.csv"
NEW_BADGE_PATH = PROJECT_ROOT / "logs" / "new-verses.csv"
SETTINGS_PATH = PROJECT_ROOT / "logs" / "settings.json"
IMAGE_DIR = PROJECT_ROOT / "logs" / "generated-images"
BLOG_LOG_PATH = PROJECT_ROOT / "logs" / "blog-log.csv"
BLOG_IMAGE_MAP_PATH = PROJECT_ROOT / "logs" / "blog-images.json"
APP_LOG_PATH = PROJECT_ROOT / "logs" / "app.log"

logger = logging.getLogger("lfl")
if not logger.handlers:
    APP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(APP_LOG_PATH, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    

def read_used_verses(path: Path) -> set[str]:
    if not path.exists():
        return set()
    verses = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("-"):
            raw = line.lstrip("- ").strip()
            verses.add(normalize_ref(raw))
    return verses


BOOK_MAP = {
    "genesis": "창세기",
    "exodus": "출애굽기",
    "leviticus": "레위기",
    "numbers": "민수기",
    "deuteronomy": "신명기",
    "joshua": "여호수아",
    "judges": "사사기",
    "ruth": "룻기",
    "1samuel": "사무엘상",
    "2samuel": "사무엘하",
    "1kings": "열왕기상",
    "2kings": "열왕기하",
    "1chronicles": "역대상",
    "2chronicles": "역대하",
    "ezra": "에스라",
    "nehemiah": "느헤미야",
    "esther": "에스더",
    "job": "욥기",
    "psalms": "시편",
    "psalm": "시편",
    "proverbs": "잠언",
    "ecclesiastes": "전도서",
    "songofsolomon": "아가",
    "isaiah": "이사야",
    "jeremiah": "예레미야",
    "lamentations": "예레미야애가",
    "ezekiel": "에스겔",
    "daniel": "다니엘",
    "hosea": "호세아",
    "joel": "요엘",
    "amos": "아모스",
    "obadiah": "오바댜",
    "jonah": "요나",
    "micah": "미가",
    "nahum": "나훔",
    "habakkuk": "하박국",
    "zephaniah": "스바냐",
    "haggai": "학개",
    "zechariah": "스가랴",
    "malachi": "말라기",
    "matthew": "마태복음",
    "mark": "마가복음",
    "luke": "누가복음",
    "john": "요한복음",
    "acts": "사도행전",
    "romans": "로마서",
    "1corinthians": "고린도전서",
    "2corinthians": "고린도후서",
    "galatians": "갈라디아서",
    "ephesians": "에베소서",
    "philippians": "빌립보서",
    "colossians": "골로새서",
    "1thessalonians": "데살로니가전서",
    "2thessalonians": "데살로니가후서",
    "1timothy": "디모데전서",
    "2timothy": "디모데후서",
    "titus": "디도서",
    "philemon": "빌레몬서",
    "hebrews": "히브리서",
    "james": "야고보서",
    "1peter": "베드로전서",
    "2peter": "베드로후서",
    "1john": "요한일서",
    "2john": "요한이서",
    "3john": "요한삼서",
    "jude": "유다서",
    "revelation": "요한계시록",
}


def normalize_ref(ref: str) -> str:
    ref = ref.strip()
    if not ref:
        return ""
    ref = ref.replace("–", "-").replace("—", "-")
    ref = re.sub(r"\s+", " ", ref)
    ref = ref.replace(" :", ":").replace(": ", ":")
    ref = ref.strip(" ,.")
    # Normalize English book names like "Hebrews11:1" -> "Hebrews 11:1"
    ref = re.sub(r"([A-Za-z])(\d)", r"\1 \2", ref)
    # Normalize Korean book names like "히브리서11:1" -> "히브리서 11:1"
    ref = re.sub(r"([가-힣]+)\s*(\d)", r"\1 \2", ref)
    m = re.match(r"^([1-3])\s*([A-Za-z]+)\s+(.+)$", ref)
    if m:
        book_key = f"{m.group(1)}{m.group(2).lower()}"
        rest = re.sub(r"[^0-9:\-]", "", m.group(3))
        if book_key in BOOK_MAP:
            return f"{BOOK_MAP[book_key]} {rest}".strip()
    m = re.match(r"^([A-Za-z]+)\s+(.+)$", ref)
    if m:
        book_key = m.group(1).lower()
        rest = re.sub(r"[^0-9:\-]", "", m.group(2))
        if book_key in BOOK_MAP:
            return f"{BOOK_MAP[book_key]} {rest}".strip()
    return ref


def parse_theme(theme: str) -> tuple[str, str]:
    cleaned = theme.strip()
    cleaned = re.sub(r"^\\d+[\\).]\\s*", "", cleaned)
    if ":" in cleaned:
        left, right = cleaned.split(":", 1)
        return left.strip(), right.strip()
    if "—" in cleaned:
        left, right = cleaned.split("—", 1)
        theme_en = left.strip()
        # right could be like "믿음 (Faith)"
        theme_ko = re.sub(r"\\(.*?\\)", "", right).strip()
        return theme_en, theme_ko
    if "/" in cleaned:
        left, right = cleaned.split("/", 1)
        return left.strip(), right.strip()
    return cleaned, ""


def has_latin(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text or ""))


def append_used_verse(path: Path, verse: str) -> None:
    verse = normalize_ref(verse)
    if not verse:
        return
    used = read_used_verses(path)
    if verse in used:
        return
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {verse}\n")


def remove_used_verse(path: Path, verse: str) -> None:
    verse = normalize_ref(verse)
    if not verse or not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if line.strip() != f"- {verse}"]
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


DEFAULT_THEME_LIST = [
    "1. The Ground Beneath:믿음",
    "2. Even So, Light:소망 / 위로",
    "3. Held Quietly:사랑",
    "4. The Gentle Joy:감사 / 기쁨",
    "5. Still Waters:평안 / 인도하심",
    "6. The Listening Room:기도 / 묵상",
    "7. Walk Bold:결단 / 용기 / 행동",
    "8. Known and Named:정체성 / 존재",
]


def read_themes(path: Path) -> list[str]:
    if not path.exists():
        return DEFAULT_THEME_LIST.copy()
    themes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d+[\\).]\\s", line):
            themes.append(line)
    return themes or DEFAULT_THEME_LIST.copy()


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    return text or "poster"


def extract_output_text(resp_json: dict) -> str:
    if "output_text" in resp_json:
        return resp_json.get("output_text", "")
    for item in resp_json.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    return ""


def call_openai(prompt: str, system_prompt: str | None = None) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    system_prompt = system_prompt or (
        "You are a design planner for the Letter for Living Bible typography posters. "
        "Return only strict JSON with no extra commentary."
    )

    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "text": {"format": {"type": "json_object"}},
    }

    start = time.monotonic()
    prompt_len = len(prompt)
    try:
        resp = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=90,
        )
    except Exception:
        elapsed = time.monotonic() - start
        logger.exception("OpenAI request failed model=%s prompt_len=%s elapsed=%.2fs", OPENAI_MODEL, prompt_len, elapsed)
        raise
    elapsed = time.monotonic() - start
    logger.info("OpenAI response model=%s prompt_len=%s status=%s elapsed=%.2fs", OPENAI_MODEL, prompt_len, resp.status_code, elapsed)
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text}")

    text = extract_output_text(resp.json())
    if not text:
        raise RuntimeError("Empty response from OpenAI")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse JSON: {exc}\n{text}")


def call_openai_text(
    prompt: str,
    system_prompt: str | None = None,
    model: str | None = None,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    system_prompt = system_prompt or "You are a helpful assistant."

    payload = {
        "model": model or OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "text": {"format": {"type": "text"}},
    }

    resp = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text}")

    text = extract_output_text(resp.json())
    if not text:
        raise RuntimeError("Empty response from OpenAI")
    return text.strip()


def select_new_verse(theme: str, used: set[str]) -> str:
    used_block = "\n".join(sorted(used)) if used else "(none)"
    prompt = f"""
너는 성경 구절을 선택하는 에디터다.
주제에 맞는 성경 구절을 한국어 책 이름 형식으로 1개만 반환하라.
이미 사용된 구절은 절대 선택하지 않는다.

주제: {theme}
이미 사용된 구절:
{used_block}

출력은 반드시 JSON만 반환한다.
{{
  "verse_reference": ""
}}
""".strip()
    for _ in range(5):
        result = call_openai(
            prompt,
            system_prompt="You return strict JSON only.",
        )
        verse_ref = normalize_ref(str(result.get("verse_reference", "")).strip())
        if verse_ref and verse_ref not in used:
            return verse_ref
    return ""


def build_prompt(
    theme: str,
    size: str,
    tone: str,
    notes: str,
    used: set[str],
    themes: list[str],
    color_mode: str,
) -> str:
    themes_block = "\n".join(themes) if themes else "(themes unavailable)"
    used_block = "\n".join(sorted(used)) if used else "(none)"

    color_text = color_mode or "(not specified)"
    return f"""
SYSTEM INSTRUCTION

너는 ‘영문 성경 말씀(ESV)을 기준으로
영업용 타이포그래피 포스터 기획서를 작성하는
전문 디자인 기획자’다.

이 작업은 ‘문서 정리’나 ‘요약’이 아니다.
아래 템플릿의 각 항목을
반드시 새로 기획하고 새로 작성해야 한다.

⚠️ 매우 중요:
- 아래에 포함된 모든 예시(ex)는 설명용이다.
- 예시 문구를 그대로 복사하거나 재사용하는 것은 금지한다.
- 출력 결과에는 예시 문구가 단 한 줄도 포함되면 안 된다.
- 모든 문장은 새로 작성해야 한다.

────────────────────

[언어 및 기준 규칙]

1. 실제 포스터 디자인에 사용되는 문장은
   반드시 영어 성경 말씀(ESV)만을 기준으로 한다.
2. 한글 문장은 설명·해석·기획용 레이어이며,
   디자인 문장으로 취급하지 않는다.
3. 모든 강조, 생략, 레이아웃 판단은
   ESV 영어 문장을 기준으로 수행한다.

────────────────────

[출력 규칙]

- 아래 템플릿의 제목과 순서를 절대 변경하지 말 것.
- 모든 항목을 빠짐없이 채울 것.
- 기획서 톤으로 간결하고 명확하게 작성할 것.
- 감성적인 수식어나 설교체 문장은 사용하지 말 것.

────────────────────

테마  
{theme}

앵커 텍스트 (디자인 언어)
- 실제 포스터 디자인에 사용할 핵심 문장 1개만 제시할 것.
- 영어 문장만 작성할 것. 설명/예시 문구는 쓰지 말 것.

말씀 출처  
- ESV 영어 성경 말씀을 먼저 작성할 것.
- 그 아래에 동일 구절의 한글 개역개정 번역을 병기할 것.
- 구절 표기는 영문/한글 각각 정확히 표기할 것.
- 각 본문은 1~2문장으로 완결된 구절 텍스트를 적을 것.
- verse_reference_en에는 영문 책 이름으로 표기할 것 (예: 2 Corinthians 5:7).

말씀의 의미  
- 핵심 의미: 영어 말씀의 신학적·메시지적 핵심을 한글로 설명
- 감정 포인트: 이 말씀이 전달하는 정서적 무게감
- 붙잡는 순간: 어떤 신앙적 상황에서 이 말씀이 힘이 되는지

핵심 강조 요소  
- 시각적으로 가장 중요한 부분:
  → ESV 영어 문장 중 타이포그래피에서
    가장 크게 또는 가장 무겁게 다뤄야 할 단어/구절
- 생략해도 되는 부분:
  → 의미를 해치지 않고
    보조적으로 축약·분해 가능한 영어 구절
- 위 두 항목은 반드시 english_verse에서 그대로 발췌한 영어 구절만 작성할 것.

디자인 가이드 (컬러, 레이아웃)  
아래 형식을 반드시 그대로 따른다. (순서/레이블 고정)

1️⃣ 문장을 디자인용 단어 단위로 해체  
- 원문: "..."  
- 이 문장은 디자인적으로 3개의 층으로 나눠야 한다.  
(A) 행위: "..."  
의미/감정 1~2줄  
(B) 기준: "..."  
의미/감정 1~2줄  
(C) 대비(부정): "..."  
의미/감정 1~2줄  
👉 A+B가 핵심이고, C는 배경으로 밀어낸다는 결론 1줄

2️⃣ 단어별 시각적 역할 정의 (핵심 3개)  
- 🔴 "핵심 동사/행위": 역할/형태/위치/시각적 인상 (각 1줄)  
- 🔵 "핵심 기준/대상": 역할/형태/위치/시각적 인상 (각 1줄)  
- ⚪ "배제/감쇠 구절": 역할/형태/위치/시각적 인상 (각 1줄)  
* 위 3개 영어 구절은 반드시 english_verse에서 직접 발췌해 따옴표로 표기한다.  
* emphasis_most는 🔵 항목에 반드시 포함, emphasis_can_drop는 ⚪ 항목에 반드시 포함.

3️⃣ 문장 구조를 디자인 구조로 재조립  
- 안 1: [작은 글자] / [큰 글자] / [아주 작은 글자]  
- 안 2: 2~3줄 변형안  
👉 “문장이 아니라 신앙의 구조를 보여준다”는 결론 1줄 포함

4️⃣ 컬러를 의미 단위로 쓰는 법  
- 배경: 컬러명 + 의미  
- 핵심: 컬러명 + 의미  
- 보조: 컬러명 + 의미  
👉 제작도수(컬러) 설정을 반드시 반영할 것

마지막 한 줄  
- “말씀을 그림으로 재현하지 않고, 영적 위계를 시각적 위계로 번역한다.”를 포함.

규칙:
- 한국어로만 작성한다. 영어는 따옴표 안의 발췌 구절만 허용.
- ESV 영어 문장을 기준으로 줄바꿈/크기/시선 흐름을 설명한다.

공간 속 사용 맥락  
- 이 포스터가 어울리는 공간
- 이 문구가 가장 잘 전달될 사람 또는 상황
(한글로 작성)

기획 의도 한 줄  
- 전체 기획을 관통하는 의도를
  한글 한 문장으로 명확히 작성할 것.
- 입력 메모에 적힌 문장을 그대로 복사하지 말고 새 문장으로 쓸 것.

────────────────────

이 템플릿을 기준으로
아래 성경 구절을 사용해 기획서를 작성하라.

[입력 구절]
- 성경 구절 (ESV): {notes or '(none)'}

프로젝트 정보:
- Themes list:\n{themes_block}
- Use the provided theme exactly.
- Avoid any verse references already used:\n{used_block}
- Do NOT recommend or return any verse from the used list.
- Size: {size} vertical.
- Color mode: {color_text}
- Tone keywords: {tone or '(none)'}
- Translations: English = ESV, Korean = 개역개정
- verse_reference는 반드시 한글 책 이름 형식으로만 작성 (예: 히브리서 11:1). 쉼표/마침표 금지.
- verse_reference_en은 반드시 영문 책 이름 형식으로만 작성 (예: 2 Corinthians 5:7).

반드시 JSON으로만 응답. 아래 구조를 유지:
{{
  "theme_en": "",
  "theme_ko": "",
  "anchor_text": "",
  "verse_reference": "",
  "verse_reference_en": "",
  "english_verse": "",
  "korean_verse": "",
  "meaning_core": "",
  "meaning_emotion": "",
  "meaning_moment": "",
  "emphasis_most": "",
  "emphasis_can_drop": "",
  "design_guide": "",
  "spatial_context": "",
  "one_line_intent": ""
}}
"""




def write_brief(data: dict, size: str) -> str:
    brief = (
        "# Letter for Living Poster Brief\n\n"
        "## Theme\n"
        f"- English: {data.get('theme_en', '')}\n"
        f"- Korean: {data.get('theme_ko', '')}\n\n"
        "## Verse\n"
        f"- Reference: {data.get('verse_reference', '')}\n"
        f"- Reference (EN): {data.get('verse_reference_en', '')}\n"
        f"- English (ESV): {data.get('english_verse', '')}\n"
        f"- Korean (개역개정): {data.get('korean_verse', '')}\n\n"
        "## 앵커 텍스트 (디자인 언어)\n"
        f"- {data.get('anchor_text', '')}\n\n"
        "## 말씀 출처\n"
        f"- {data.get('verse_reference', '')}\n"
        f"- {data.get('verse_reference_en', '')}\n"
        f"- {data.get('english_verse', '')}\n"
        f"- {data.get('korean_verse', '')}\n\n"
        "## 말씀의 의미\n"
        f"- 핵심 의미: {data.get('meaning_core', '')}\n"
        f"- 감정 포인트: {data.get('meaning_emotion', '')}\n"
        f"- 붙잡는 순간: {data.get('meaning_moment', '')}\n\n"
        "## 핵심 강조 요소\n"
        f"- 가장 중요한 부분: {data.get('emphasis_most', '')}\n"
        f"- 생략 가능 부분: {data.get('emphasis_can_drop', '')}\n\n"
        "## 디자인 가이드 (컬러/레이아웃)\n"
        f"{data.get('design_guide', '')}\n\n"
        "## 공간 속 사용 맥락\n"
        f"- {data.get('spatial_context', '')}\n\n"
        "## 기획 의도 한 줄\n"
        f"- {data.get('one_line_intent', '')}\n\n"
        "## Production Notes\n"
        f"- Size: {size} vertical\n"
    )
    return brief


def append_log(data: dict, size: str, brief_path: Path) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        with LOG_PATH.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "date",
                    "theme",
                    "verse_reference",
                    "english_title",
                    "korean_title",
                    "size",
                    "palette",
                    "layout_summary",
                    "file_paths",
                    "notes",
                ]
            )

    layout_summary = data.get("design_guide", "")
    palette = data.get("color_mode", "")
    file_paths = f"{brief_path}"

    with LOG_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                dt.date.today().isoformat(),
                data.get("theme_display", "") or data.get("theme_en", ""),
                data.get("verse_reference", ""),
                data.get("anchor_text", ""),
                data.get("meaning_core", ""),
                size,
                palette,
                layout_summary,
                file_paths,
                "",
            ]
        )


def load_brief_links(log_path: Path, project_root: Path) -> dict[str, str]:
    if not log_path.exists():
        return {}
    links: dict[str, str] = {}
    with log_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            verse = (row.get("verse_reference") or "").strip()
            file_paths = (row.get("file_paths") or "").strip()
            if not verse or not file_paths:
                continue
            brief_path = file_paths.split(";", 1)[0].strip()
            if not brief_path:
                continue
            try:
                brief = Path(brief_path).resolve()
            except Exception:
                continue
            if not brief.exists():
                continue
            try:
                rel = brief.relative_to(project_root)
            except ValueError:
                continue
            links[verse] = str(rel)
    return links


def parse_brief_file(path: Path) -> dict:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    design_lines: list[str] = []
    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section == "디자인 가이드 (컬러/레이아웃)":
            if line.startswith("- "):
                design_lines.append(line[2:].strip())
            elif line:
                design_lines.append(line)
            continue
        if not line.startswith("- "):
            continue
        content = line[2:].strip()
        if section == "Theme":
            if content.startswith("English:"):
                result["theme_en"] = content.replace("English:", "").strip()
            elif content.startswith("Korean:"):
                result["theme_ko"] = content.replace("Korean:", "").strip()
        elif section == "Verse":
            if content.startswith("Reference:"):
                result["verse_reference"] = content.replace("Reference:", "").strip()
            elif content.startswith("Reference (EN):"):
                result["verse_reference_en"] = content.replace("Reference (EN):", "").strip()
            elif content.startswith("English (ESV):"):
                result["english_verse"] = content.replace("English (ESV):", "").strip()
            elif content.startswith("Korean (개역개정):"):
                result["korean_verse"] = content.replace("Korean (개역개정):", "").strip()
        elif section == "앵커 텍스트 (디자인 언어)":
            result["anchor_text"] = content
        elif section == "말씀의 의미":
            if content.startswith("핵심 의미:"):
                result["meaning_core"] = content.replace("핵심 의미:", "").strip()
            elif content.startswith("감정 포인트:"):
                result["meaning_emotion"] = content.replace("감정 포인트:", "").strip()
            elif content.startswith("붙잡는 순간:"):
                result["meaning_moment"] = content.replace("붙잡는 순간:", "").strip()
        elif section == "핵심 강조 요소":
            if content.startswith("가장 중요한 부분:"):
                result["emphasis_most"] = content.replace("가장 중요한 부분:", "").strip()
            elif content.startswith("생략 가능 부분:"):
                result["emphasis_can_drop"] = content.replace("생략 가능 부분:", "").strip()
        elif section == "공간 속 사용 맥락":
            result["spatial_context"] = content
        elif section == "기획 의도 한 줄":
            result["one_line_intent"] = content
    if design_lines:
        result["design_guide"] = "\n".join(design_lines)
    return result


def load_brief_entries(
    project_root: Path, log_path: Path, briefs_dir: Path, theme_order: list[str]
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    logged_paths: set[str] = set()
    if log_path.exists():
        with log_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                file_paths = (row.get("file_paths") or "").strip()
                brief_path = file_paths.split(";", 1)[0].strip() if file_paths else ""
                if not brief_path:
                    continue
                try:
                    rel = str(Path(brief_path).resolve().relative_to(project_root))
                except Exception:
                    continue
                logged_paths.add(rel)
                raw_theme = (row.get("theme") or "").strip()
                entries.append(
                    {
                        "date": (row.get("date") or "").strip(),
                        "theme": normalize_theme_display(raw_theme, theme_order) if raw_theme else "",
                        "verse_reference": (row.get("verse_reference") or "").strip(),
                        "brief_path": rel,
                        "source": "log",
                    }
                )
    if briefs_dir.exists():
        for brief in sorted(briefs_dir.glob("*.md")):
            try:
                rel = str(brief.resolve().relative_to(project_root))
            except Exception:
                continue
            if rel in logged_paths:
                continue
            entries.append(
                {
                    "date": "",
                    "theme": "미기록",
                    "verse_reference": "",
                    "brief_path": rel,
                    "source": "file",
                }
            )
    return entries


def build_used_entries(
    used_list: list[str],
    theme_map: dict[str, str],
    theme_order: list[str],
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for verse in sorted(used_list):
        raw_theme = theme_map.get(verse, "미분류")
        theme = normalize_theme_display(raw_theme, theme_order) if raw_theme else "미분류"
        entries.append(
            {
                "verse_reference": verse,
                "theme": theme,
            }
        )
    return entries


def append_blog_log(data: dict, result: dict) -> None:
    BLOG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not BLOG_LOG_PATH.exists():
        with BLOG_LOG_PATH.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["date", "title", "theme", "verse_reference", "hashtags", "body_preview"]
            )
    body = (data.get("body") or "").strip().replace("\n", " ")
    preview = body[:140]
    with BLOG_LOG_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                dt.date.today().isoformat(),
                data.get("title", ""),
                result.get("theme_display", "") or result.get("theme_en", ""),
                result.get("verse_reference", ""),
                data.get("hashtags", ""),
                preview,
            ]
        )


def normalize_blog_result(payload: dict) -> dict:
    title = str(payload.get("title", "")).strip()
    body = str(payload.get("body", "")).strip()
    hashtags = str(payload.get("hashtags", "")).strip()

    def has_hashtag_line(line: str) -> bool:
        return bool(re.search(r"#\\S+", line))

    if body:
        lines = [line.rstrip() for line in body.splitlines()]
        idx = len(lines) - 1
        while idx >= 0 and not lines[idx].strip():
            idx -= 1
        if idx >= 0:
            last_line = lines[idx].strip()
            if not hashtags and has_hashtag_line(last_line):
                hashtags = last_line
                lines = lines[:idx]
            elif hashtags and last_line == hashtags:
                lines = lines[:idx]
        while lines and not lines[-1].strip():
            lines.pop()
        body = "\n".join(lines).strip()

    payload["title"] = title
    payload["body"] = body
    payload["hashtags"] = hashtags
    return payload


def load_blog_history(limit: int = 30) -> list[dict[str, str]]:
    if not BLOG_LOG_PATH.exists():
        return []
    rows: list[dict[str, str]] = []
    with BLOG_LOG_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "date": (row.get("date") or "").strip(),
                    "title": (row.get("title") or "").strip(),
                    "theme": (row.get("theme") or "").strip(),
                    "verse_reference": (row.get("verse_reference") or "").strip(),
                    "hashtags": (row.get("hashtags") or "").strip(),
                    "body_preview": (row.get("body_preview") or "").strip(),
                }
            )
    return list(reversed(rows))[:limit]


def load_blog_images(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_blog_images(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_used_theme_map(log_path: Path) -> dict[str, str]:
    if not log_path.exists():
        return {}
    theme_map: dict[str, str] = {}
    with log_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            verse = (row.get("verse_reference") or "").strip()
            theme = (row.get("theme") or "").strip()
            if verse and theme:
                theme_map[verse] = theme
    return theme_map


def load_theme_overrides(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    overrides: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            verse = (row.get("verse_reference") or "").strip()
            theme = (row.get("theme") or "").strip()
            if verse and theme:
                overrides[verse] = theme
    return overrides


def save_theme_override(path: Path, verse: str, theme: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(
                    {
                        "verse_reference": (row.get("verse_reference") or "").strip(),
                        "theme": (row.get("theme") or "").strip(),
                    }
                )
    updated = False
    for row in rows:
        if row["verse_reference"] == verse:
            row["theme"] = theme
            updated = True
            break
    if not updated:
        rows.append({"verse_reference": verse, "theme": theme})
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["verse_reference", "theme"])
        for row in rows:
            if row["verse_reference"] and row["theme"]:
                writer.writerow([row["verse_reference"], row["theme"]])


def load_new_badges(path: Path, now: dt.datetime) -> set[str]:
    if not path.exists():
        return set()
    recent: list[tuple[str, dt.datetime]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            verse = (row.get("verse_reference") or "").strip()
            raw_time = (row.get("created_at") or "").strip()
            if not verse or not raw_time:
                continue
            try:
                created_at = dt.datetime.fromisoformat(raw_time)
            except ValueError:
                continue
            if now - created_at <= dt.timedelta(days=1):
                recent.append((verse, created_at))
    # prune expired entries
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["verse_reference", "created_at"])
        for verse, created_at in recent:
            writer.writerow([verse, created_at.isoformat(timespec="seconds")])
    return {verse for verse, _ in recent}


def save_new_badge(path: Path, verse: str, now: dt.datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(
                    {
                        "verse_reference": (row.get("verse_reference") or "").strip(),
                        "created_at": (row.get("created_at") or "").strip(),
                    }
                )
    updated = False
    for row in rows:
        if row["verse_reference"] == verse:
            row["created_at"] = now.isoformat(timespec="seconds")
            updated = True
            break
    if not updated:
        rows.append({"verse_reference": verse, "created_at": now.isoformat(timespec="seconds")})
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["verse_reference", "created_at"])
        for row in rows:
            if row["verse_reference"]:
                writer.writerow([row["verse_reference"], row["created_at"]])


def normalize_theme_display(theme: str, theme_order: list[str]) -> str:
    if theme in theme_order:
        return theme
    theme_lookup = {parse_theme(item)[0].lower(): item for item in theme_order}
    theme_en, theme_ko = parse_theme(theme)
    if theme_en and theme_en.lower() in theme_lookup:
        return theme_lookup[theme_en.lower()]
    if theme_ko:
        for item in theme_order:
            if parse_theme(item)[1] == theme_ko:
                return item
    return theme


def group_used_by_theme(used_list: list[str], theme_map: dict[str, str], theme_order: list[str]) -> list[tuple[str, list[str]]]:
    order_index = {theme: idx for idx, theme in enumerate(theme_order)}
    grouped: dict[str, list[str]] = {}
    for verse in used_list:
        raw_theme = theme_map.get(verse, "미분류")
        theme = normalize_theme_display(raw_theme, theme_order)
        grouped.setdefault(theme, []).append(verse)
    def sort_key(item: tuple[str, list[str]]) -> tuple[int, str]:
        theme = item[0]
        return (order_index.get(theme, 10_000), theme)
    return [(theme, verses) for theme, verses in sorted(grouped.items(), key=sort_key)]


@app.route("/planner", methods=["GET", "POST"])
def planner():
    themes = read_themes(THEMES_PATH)
    used = read_used_verses(USED_VERSES_PATH)
    brief_links = load_brief_links(LOG_PATH, PROJECT_ROOT)
    used_theme_map = load_used_theme_map(LOG_PATH)
    theme_overrides = load_theme_overrides(THEME_MAP_PATH)
    used_theme_map.update(theme_overrides)
    used_theme_map = {
        verse: normalize_theme_display(theme, themes) for verse, theme in used_theme_map.items()
    }
    now = dt.datetime.now()
    new_badges = load_new_badges(NEW_BADGE_PATH, now)
    error = request.args.get("error") if request.args.get("error") else None
    notice = request.args.get("notice") if request.args.get("notice") else None
    new_verse = request.args.get("new") if request.args.get("new") else None
    if request.method == "GET" and not session.pop("preserve_planner_result", False):
        session.pop("last_result", None)
    result = session.get("last_result")
    selected_theme = ""

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "add_used":
            verse_ref = request.form.get("verse_reference", "").strip()
            modal_flag = request.form.get("modal", "").strip()
            if not verse_ref:
                error = "추가할 말씀을 입력해 주세요."
            elif normalize_ref(verse_ref) in used:
                error = "이미 등록된 말씀입니다."
            else:
                verse_ref = normalize_ref(verse_ref)
                append_used_verse(USED_VERSES_PATH, verse_ref)
                save_new_badge(NEW_BADGE_PATH, verse_ref, now)
                notice = "제작된 말씀에 추가했습니다."
                new_verse = verse_ref
            used = read_used_verses(USED_VERSES_PATH)
            modal_arg = "1" if modal_flag else None
            return redirect(url_for("planner", notice=notice, error=error, new=new_verse, modal=modal_arg))
        if action == "remove_used":
            verse_ref = request.form.get("verse_reference", "").strip()
            modal_flag = request.form.get("modal", "").strip()
            if verse_ref:
                remove_used_verse(USED_VERSES_PATH, verse_ref)
                notice = "제작된 말씀에서 삭제했습니다."
            used = read_used_verses(USED_VERSES_PATH)
            modal_arg = "1" if modal_flag else None
            return redirect(url_for("planner", notice=notice, error=error, modal=modal_arg))
        if action == "set_used_theme":
            verse_ref = request.form.get("verse_reference", "").strip()
            theme_value = request.form.get("theme_value", "").strip()
            modal_flag = request.form.get("modal", "").strip()
            if not verse_ref:
                error = "말씀을 선택해 주세요."
            elif theme_value not in themes:
                error = "주제를 8가지 중에서 선택해 주세요."
            else:
                save_theme_override(THEME_MAP_PATH, normalize_ref(verse_ref), theme_value)
                notice = "주제 분류를 저장했습니다."
            modal_arg = "1" if modal_flag else None
            return redirect(url_for("planner", notice=notice, error=error, modal=modal_arg))
        if action == "set_used_theme_bulk":
            modal_flag = request.form.get("modal", "").strip()
            delete_verse = request.form.get("delete_verse", "").strip()
            if delete_verse:
                remove_used_verse(USED_VERSES_PATH, delete_verse)
                notice = "제작된 말씀에서 삭제했습니다."
            else:
                verses = request.form.getlist("verse_reference")
                theme_values = request.form.getlist("theme_value")
                saved = 0
                for verse_ref, theme_value in zip(verses, theme_values):
                    verse_ref = normalize_ref(verse_ref)
                    theme_value = theme_value.strip()
                    if not verse_ref or theme_value not in themes:
                        continue
                    save_theme_override(THEME_MAP_PATH, verse_ref, theme_value)
                    saved += 1
                if saved:
                    notice = "주제 분류를 저장했습니다."
            modal_arg = "1" if modal_flag else None
            return redirect(url_for("planner", notice=notice, error=error, modal=modal_arg))
        if action == "confirm":
            verse_ref = request.form.get("verse_reference", "").strip()
            theme_value = request.form.get("theme_value", "").strip()
            if verse_ref:
                verse_ref = normalize_ref(verse_ref)
                append_used_verse(USED_VERSES_PATH, verse_ref)
                save_new_badge(NEW_BADGE_PATH, verse_ref, now)
                if theme_value in themes:
                    save_theme_override(THEME_MAP_PATH, verse_ref, theme_value)
                notice = "제작된 말씀에 추가했습니다."
                new_verse = verse_ref
            used = read_used_verses(USED_VERSES_PATH)
            return redirect(url_for("planner", notice=notice, error=error, new=new_verse))

        theme = request.form.get("theme", "").strip()
        selected_theme = theme
        size_family = request.form.get("size_family", "").strip()
        size = request.form.get("size", "A2").strip()
        custom_size = request.form.get("custom_size", "").strip()
        color_mode = request.form.get("color_mode", "").strip()
        if not theme:
            error = "주제를 선택해 주세요."
        elif not size_family:
            error = "규격표준을 선택해 주세요."
        elif not color_mode:
            error = "제작도수(컬러)를 선택해 주세요."
        elif size_family == "custom" and not custom_size:
            error = "직접입력 사이즈를 입력해 주세요."
        if custom_size:
            size = custom_size
        tone = request.form.get("tone", "").strip()
        notes = request.form.get("notes", "").strip()
        chosen_verse = ""

        if not error and theme not in themes:
            error = "주제를 8가지 중에서 선택해 주세요."
        if not error and notes:
            note_text = notes.strip()
            if note_text:
                for used_ref in used:
                    if used_ref and used_ref in note_text:
                        error = "이미 제작된 말씀입니다. 다른 말씀으로 다시 시도해 주세요."
                        break
                if not error:
                    chosen_verse = note_text
        if not error and not chosen_verse:
            chosen_verse = select_new_verse(theme, used)
            if not chosen_verse:
                error = "새로운 말씀을 찾지 못했습니다. 다시 시도해 주세요."
        if not error:
            prompt = build_prompt(theme, size, tone, chosen_verse, used, themes, color_mode)
            try:
                result = None
                verse_ref = ""
                retry_note = ""
                for _ in range(6):
                    result = call_openai(prompt + retry_note)
                    result["color_mode"] = color_mode
                    verse_ref = normalize_ref(result.get("verse_reference", ""))
                    if not verse_ref:
                        retry_note = "\n\n주의: verse_reference가 비어 있습니다. 반드시 채워주세요."
                        continue
                    if verse_ref in used:
                        retry_note = (
                            f"\n\n주의: 직전 결과가 사용된 말씀({verse_ref})이었습니다. "
                            "반드시 다른 구절을 선택하세요."
                        )
                        continue
                    english_verse = str(result.get("english_verse", "")).strip()
                    korean_verse = str(result.get("korean_verse", "")).strip()
                    if not english_verse or not korean_verse:
                        retry_note = (
                            "\n\n주의: english_verse 또는 korean_verse가 비어 있습니다. "
                            "ESV 영어 본문과 개역개정 한글 본문을 모두 작성하세요."
                        )
                        continue
                    verse_reference_en = str(result.get("verse_reference_en", "")).strip()
                    if not verse_reference_en or not has_latin(verse_reference_en):
                        retry_note = (
                            "\n\n주의: verse_reference_en이 비어 있거나 영어 책 이름이 아닙니다. "
                            "예: 2 Corinthians 5:7"
                        )
                        continue
                    korean_only_fields = [
                        "meaning_core",
                        "meaning_emotion",
                        "meaning_moment",
                        "spatial_context",
                        "one_line_intent",
                    ]
                    bad_field = ""
                    for key in korean_only_fields:
                        if has_latin(str(result.get(key, ""))):
                            bad_field = key
                            break
                    if bad_field:
                        retry_note = (
                            f"\n\n주의: {bad_field} 필드에 영어가 포함되었습니다. "
                            "해당 필드들은 반드시 한국어로만 작성하세요."
                        )
                        continue
                    emphasis_most = str(result.get("emphasis_most", "")).strip()
                    emphasis_can_drop = str(result.get("emphasis_can_drop", "")).strip()
                    if (
                        not emphasis_most
                        or not emphasis_can_drop
                        or not english_verse
                        or not emphasis_most.lower() in english_verse.lower()
                        or not emphasis_can_drop.lower() in english_verse.lower()
                        or not has_latin(emphasis_most)
                        or not has_latin(emphasis_can_drop)
                    ):
                        retry_note = (
                            "\n\n주의: emphasis_most/emphasis_can_drop는 "
                            "english_verse에서 그대로 발췌한 영어 구절이어야 합니다."
                        )
                        continue
                    design_guide = str(result.get("design_guide", "")).strip()
                    if (
                        not design_guide
                        or emphasis_most.lower() not in design_guide.lower()
                        or emphasis_can_drop.lower() not in design_guide.lower()
                    ):
                        retry_note = (
                            "\n\n주의: design_guide에는 emphasis_most와 "
                            "emphasis_can_drop를 영어 원문 그대로 포함해야 합니다."
                        )
                        continue
                    design_guide_cleaned = re.sub(r"\"[^\"]*\"", "", design_guide)
                    if has_latin(design_guide_cleaned):
                        retry_note = (
                            "\n\n주의: design_guide 설명은 한국어로만 작성하세요. "
                            "영어는 따옴표 안의 발췌 구절만 허용됩니다."
                        )
                        continue
                    one_line_intent = str(result.get("one_line_intent", "")).strip()
                    if notes and one_line_intent and one_line_intent in notes:
                        retry_note = (
                            "\n\n주의: one_line_intent가 메모 문구를 그대로 복사했습니다. "
                            "새로운 한국어 문장으로 다시 작성하세요."
                        )
                        continue
                    break
                if not verse_ref or verse_ref in used:
                    raise RuntimeError("새로운 말씀을 찾지 못했습니다. 다시 시도해 주세요.")
                result["verse_reference"] = verse_ref
                theme_en, theme_ko = parse_theme(theme)
                result["theme_en"] = theme_en
                result["theme_ko"] = theme_ko
                result["theme_display"] = selected_theme

                BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

                if selected_theme in themes:
                    save_theme_override(THEME_MAP_PATH, verse_ref, selected_theme)

                theme_slug = slugify(result.get("theme_en", ""))
                verse_slug = slugify(verse_ref.replace(":", "-"))
                date_tag = dt.date.today().strftime("%Y%m%d")
                base_name = f"{date_tag}_{theme_slug}_{verse_slug}"

                brief_text = write_brief(result, size)
                brief_path = BRIEFS_DIR / f"{base_name}.md"
                brief_path.write_text(brief_text, encoding="utf-8")

                append_log(result, size, brief_path)
                session["last_result"] = result
                session["preserve_planner_result"] = True
                return redirect(url_for("planner", notice="기획서가 생성되었습니다."))
            except Exception as exc:
                error = str(exc)

    return render_template(
        "index.html",
        themes=themes,
        used_count=len(used),
        used_list=sorted(used),
        used_by_theme=group_used_by_theme(sorted(used), used_theme_map, themes),
        used_theme_map=used_theme_map,
        new_badges=new_badges,
        brief_links=brief_links,
        error=error,
        notice=notice,
        new_verse=new_verse,
        result=result,
        selected_theme=selected_theme,
    )


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/settings", methods=["GET", "POST"])
def settings():
    notice = None
    settings_data = load_settings(SETTINGS_PATH)
    if request.method == "POST":
        if request.form.get("reset_settings") == "1":
            settings_data = {}
        else:
            settings_data["naver_id"] = request.form.get("naver_id", "").strip()
            settings_data["naver_password"] = request.form.get("naver_password", "").strip()
            settings_data["naver_write_url"] = request.form.get("naver_write_url", "").strip()
            settings_data["chrome_profile_dir"] = request.form.get("chrome_profile_dir", "").strip()
        settings_data["openai_api_key"] = request.form.get("openai_api_key", "").strip()
        save_settings(SETTINGS_PATH, settings_data)
        if settings_data.get("openai_api_key"):
            os.environ["OPENAI_API_KEY"] = settings_data["openai_api_key"]
        notice = "설정이 저장되었습니다."
    return render_template("settings.html", notice=notice, settings_data=settings_data)


@app.route("/brief")
def brief():
    rel = request.args.get("file", "").strip()
    if not rel:
        abort(404)
    target = (PROJECT_ROOT / rel).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        abort(403)
    if not target.exists():
        abort(404)
    content = target.read_text(encoding="utf-8")
    return render_template("brief.html", content=content, file=str(rel))


@app.route("/blog", methods=["GET", "POST"])
def blog():
    if request.method == "GET" and not session.pop("preserve_blog_result", False):
        session.pop("last_blog", None)
        session.pop("last_image_prompt", None)
        session.pop("current_draft_id", None)
        session.pop("last_image_path", None)
        session.pop("last_image_paths", None)
    result = session.get("last_result")
    blog_result = session.get("last_blog")
    image_prompt = session.get("last_image_prompt")
    draft_id = session.get("current_draft_id")
    error = session.pop("flash_error", None)
    notice = session.pop("flash_notice", None)
    settings_data = load_settings(SETTINGS_PATH)
    themes = read_themes(THEMES_PATH)
    used = read_used_verses(USED_VERSES_PATH)
    used_theme_map = load_used_theme_map(LOG_PATH)
    theme_overrides = load_theme_overrides(THEME_MAP_PATH)
    used_theme_map.update(theme_overrides)
    used_theme_map = {
        verse: normalize_theme_display(theme, themes) for verse, theme in used_theme_map.items()
    }
    used_entries = build_used_entries(sorted(used), used_theme_map, themes)
    blog_history = load_blog_history()
    blog_images = load_blog_images(BLOG_IMAGE_MAP_PATH)
    if draft_id:
        image_paths = blog_images.get(str(draft_id))
    else:
        image_paths = session.get("last_image_paths")
    if isinstance(image_paths, str):
        image_paths = [image_paths]
    if not isinstance(image_paths, list):
        image_paths = []
    selected_brief_label = session.get("selected_brief_label", "")
    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "upload_image":
            files = request.files.getlist("image_file")
            files = [file for file in files if file and file.filename]
            if not files:
                session["flash_error"] = "이미지 파일을 선택해 주세요."
            else:
                if not draft_id:
                    session["flash_error"] = "먼저 초안을 생성해 주세요."
                    return redirect(url_for("blog"))
                IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                saved_paths: list[str] = []
                for file in files[:2]:
                    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
                    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    dest = IMAGE_DIR / f"{timestamp}_{safe_name}"
                    file.save(dest)
                    saved_paths.append(str(dest))
                blog_images[str(draft_id)] = saved_paths
                save_blog_images(BLOG_IMAGE_MAP_PATH, blog_images)
                session["last_image_paths"] = saved_paths
                session["preserve_blog_result"] = True
                session["flash_notice"] = "이미지를 업로드했습니다."
            return redirect(url_for("blog"))
        if action == "regenerate_images":
            if not blog_result:
                session["flash_error"] = "먼저 블로그 글을 생성해 주세요."
                return redirect(url_for("blog"))
            if not draft_id:
                session["flash_error"] = "초안 ID를 찾을 수 없습니다. 다시 생성해 주세요."
                return redirect(url_for("blog"))
            if not isinstance(image_prompt, list) or not image_prompt:
                session["flash_error"] = "이미지 프롬프트가 없습니다. 초안을 다시 생성해 주세요."
                return redirect(url_for("blog"))
            prompts = [item["text"] for item in image_prompt if isinstance(item, dict) and item.get("text")]
            if not prompts:
                session["flash_error"] = "이미지 프롬프트가 비어 있습니다."
                return redirect(url_for("blog"))
            try:
                images_dir = PROJECT_ROOT / "logs" / "blog-images"
                generated_paths = generate_images(prompts, images_dir, size="1024x1024")
                if generated_paths:
                    blog_images[str(draft_id)] = [str(path) for path in generated_paths]
                    save_blog_images(BLOG_IMAGE_MAP_PATH, blog_images)
                    session["last_image_paths"] = [str(path) for path in generated_paths]
                    session["preserve_blog_result"] = True
                    session["flash_notice"] = "이미지를 다시 생성했습니다."
                else:
                    session["flash_error"] = "이미지 생성 결과가 없습니다."
            except Exception as exc:
                logger.exception("Blog image regeneration failed")
                session["flash_error"] = f"블로그 이미지 재생성 실패: {exc}"
            return redirect(url_for("blog"))
        if action == "load_used_verse":
            verse_ref = request.form.get("verse_reference", "").strip()
            verse_ref = normalize_ref(verse_ref)
            if not verse_ref:
                error = "선택할 말씀이 없습니다."
            else:
                raw_theme = used_theme_map.get(verse_ref, "미분류")
                theme_en, theme_ko = parse_theme(raw_theme)
                result = {
                    "theme_en": theme_en,
                    "theme_ko": theme_ko,
                    "theme_display": normalize_theme_display(raw_theme, themes)
                    if raw_theme
                    else "",
                    "verse_reference": verse_ref,
                    "verse_reference_en": "",
                    "english_verse": "",
                    "korean_verse": "",
                    "anchor_text": "",
                    "one_line_intent": "",
                }
                session["last_result"] = result
                parts = [raw_theme or "미분류", verse_ref]
                selected_brief_label = " · ".join(part for part in parts if part)
                session["selected_brief_label"] = selected_brief_label
            return render_template(
                "blog.html",
                result=result,
                blog_result=blog_result,
                error=error,
                notice=notice,
                settings_data=settings_data,
                used_entries=used_entries,
                selected_brief_label=selected_brief_label,
            )
        if action == "open_naver_writer":
            if not blog_result:
                session["flash_error"] = "먼저 블로그 글을 생성해 주세요."
                return redirect(url_for("blog"))
            else:
                write_url = settings_data.get("naver_write_url", "").strip()
                if not write_url:
                    session["flash_error"] = "네이버 글쓰기 URL을 설정해 주세요."
                    return redirect(url_for("blog"))
                else:
                    title = blog_result.get("title", "")
                    body = blog_result.get("body", "")
                    hashtags = blog_result.get("hashtags", "")
                    full_body = body + ("\n\n" + hashtags if hashtags else "")
                    try:
                        profile_dir = settings_data.get("chrome_profile_dir", "").strip()
                        if not profile_dir:
                            profile_dir = str(Path.home() / "Library/Application Support/LetterForLivingChrome")
                        if draft_id:
                            image_paths = blog_images.get(str(draft_id))
                        else:
                            image_paths = session.get("last_image_paths")
                        if isinstance(image_paths, str):
                            image_paths = [image_paths]
                        if not isinstance(image_paths, list):
                            image_paths = []
                        thread = threading.Thread(
                            target=open_naver_writer,
                            kwargs={
                                "write_url": write_url,
                                "naver_id": settings_data.get("naver_id", ""),
                                "naver_password": settings_data.get("naver_password", ""),
                                "title": title,
                                "body": full_body,
                                "profile_dir": profile_dir,
                                "project_root": PROJECT_ROOT,
                                "image_paths": image_paths,
                            },
                            daemon=True,
                        )
                        thread.start()
                        session["preserve_blog_result"] = True
                        session["flash_notice"] = "브라우저를 열었습니다. 로그인 후 자동 입력이 진행됩니다."
                        return redirect(url_for("blog"))
                    except Exception as exc:
                        session["flash_error"] = str(exc)
                        return redirect(url_for("blog"))
        if action == "generate_blog":
            if not result:
                session["flash_error"] = "기획 생성 결과가 없습니다. 먼저 기획을 생성해 주세요."
                return redirect(url_for("blog"))
            else:
                hashtags_count = int(request.form.get("hashtags_count", "7") or 7)
                site_link = request.form.get("site_link", "").strip()
                prompt = build_blog_prompt(
                    result,
                    "",
                    "",
                    hashtags_count,
                    site_link,
                    "",
                )
                try:
                    blog_result = normalize_blog_result(call_openai(prompt))
                    session["last_blog"] = blog_result
                    draft_id = f"{dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(2).hex()}"
                    session["current_draft_id"] = draft_id
                    session["preserve_blog_result"] = True
                    append_blog_log(blog_result, result)
                    theme = result.get("theme_display", "") or result.get("theme_en", "")
                    verse = result.get("verse_reference", "")
                    anchor = result.get("anchor_text", "")
                    intent = result.get("one_line_intent", "")
                    verse_en = result.get("verse_reference_en", "") or verse
                    scripture_ko = result.get("korean_verse", "")
                    scripture_en = result.get("english_verse", "")
                    scripture_text = scripture_ko or scripture_en
                    base_prompt = (
                        "A classical-style biblical painting depicting the scene described in the scripture.\n\n"
                        f"Scripture (for scene extraction): {scripture_text or verse_en}\n"
                        f"Verse reference: {verse_en}\n"
                        f"Theme: {theme}\n\n"
                        "Scene description:\n"
                        "- Time period: biblical era (Old Testament or 1st century)\n"
                        "- Location: state the place described in the scripture\n"
                        "- Characters: the people described in the scripture, with relationships\n"
                        "- Action: depict the action described in the scripture\n\n"
                        "Composition:\n"
                        "- Perspective: medium-wide, painterly composition\n"
                        "- Focus: the central action described in the scripture\n"
                        "- Background: historically accurate environment of the biblical world\n\n"
                        "Mood & lighting:\n"
                        "- Reverent, solemn, sacred atmosphere\n"
                        "- Soft, natural light emphasizing spiritual significance\n"
                        "- Calm and dignified tone, no exaggerated drama\n\n"
                        "Style:\n"
                        "- classical religious painting\n"
                        "- realistic anatomy and fabric\n"
                        "- oil painting texture\n"
                        "- muted, earthy color palette\n"
                        "- high detail, museum-quality artwork\n\n"
                        "Restrictions:\n"
                        "- no modern elements\n"
                        "- no text or inscriptions\n"
                        "- no stylization, no cartoon\n"
                        "- no fantasy elements"
                    )
                    image_prompt = [
                        {
                            "label": "말씀 구절",
                            "text": base_prompt
                            + "\n\nSection focus:\nA quiet, anchored image that can sit before the verse itself."
                            + "\nScene cues:\nAncient stone room at dawn, clay oil lamp, linen cloth, soft shadows.",
                        },
                        {
                            "label": "본론",
                            "text": base_prompt
                            + "\n\nSection focus:\nA reflective moment that deepens the theme without explaining it."
                            + "\nScene cues:\nHands resting on a stone ledge, distant hills, muted sky.",
                        },
                    ]
                    session["last_image_prompt"] = image_prompt
                    session["flash_notice"] = "초안을 생성했습니다. 이미지가 필요하면 생성 버튼을 눌러주세요."
                    return redirect(url_for("blog"))
                except Exception as exc:
                    logger.exception("Blog draft generation failed")
                    session["flash_error"] = str(exc)
                    return redirect(url_for("blog"))
    return render_template(
        "blog.html",
        result=result,
        blog_result=blog_result,
        error=error,
        notice=notice,
        settings_data=settings_data,
        used_entries=used_entries,
        selected_brief_label=selected_brief_label,
        image_prompt=image_prompt,
        image_paths=image_paths,
        blog_history=blog_history,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(debug=True, port=port)
