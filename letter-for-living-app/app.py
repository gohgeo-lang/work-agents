import csv
import datetime as dt
import json
import os
import re
import threading
import logging
import time
import uuid
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for, send_file

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

BLOG_JOBS: dict[str, dict] = {}
BLOG_JOBS_LOCK = threading.Lock()
IMAGE_JOBS: dict[str, dict] = {}
IMAGE_JOBS_LOCK = threading.Lock()
PLANNER_JOBS: dict[str, dict] = {}
PLANNER_JOBS_LOCK = threading.Lock()


def init_blog_job() -> str:
    job_id = uuid.uuid4().hex
    with BLOG_JOBS_LOCK:
        BLOG_JOBS[job_id] = {
            "status": "running",
            "progress": 0,
            "logs": [],
            "error": None,
            "result": None,
        }
    return job_id


def update_blog_job(job_id: str, *, status: str | None = None, progress: int | None = None) -> None:
    with BLOG_JOBS_LOCK:
        job = BLOG_JOBS.get(job_id)
        if not job:
            return
        if status is not None:
            job["status"] = status
        if progress is not None:
            job["progress"] = progress


def append_blog_job_log(job_id: str, message: str, progress: int | None = None) -> None:
    with BLOG_JOBS_LOCK:
        job = BLOG_JOBS.get(job_id)
        if not job:
            return
        job["logs"].append(message)
        if progress is not None:
            job["progress"] = progress


def complete_blog_job(job_id: str, result: dict) -> None:
    with BLOG_JOBS_LOCK:
        job = BLOG_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed"
        job["progress"] = 100
        job["result"] = result


def fail_blog_job(job_id: str, message: str) -> None:
    with BLOG_JOBS_LOCK:
        job = BLOG_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "failed"
        job["error"] = message


def init_image_job() -> str:
    job_id = uuid.uuid4().hex
    with IMAGE_JOBS_LOCK:
        IMAGE_JOBS[job_id] = {
            "status": "running",
            "progress": 0,
            "logs": [],
            "error": None,
            "result": None,
        }
    return job_id


def append_image_job_log(job_id: str, message: str, progress: int | None = None) -> None:
    with IMAGE_JOBS_LOCK:
        job = IMAGE_JOBS.get(job_id)
        if not job:
            return
        job["logs"].append(message)
        if progress is not None:
            job["progress"] = progress


def complete_image_job(job_id: str, result: dict) -> None:
    with IMAGE_JOBS_LOCK:
        job = IMAGE_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed"
        job["progress"] = 100
        job["result"] = result


def fail_image_job(job_id: str, message: str) -> None:
    with IMAGE_JOBS_LOCK:
        job = IMAGE_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "failed"
        job["error"] = message


def init_planner_job(job_type: str) -> str:
    job_id = uuid.uuid4().hex
    with PLANNER_JOBS_LOCK:
        PLANNER_JOBS[job_id] = {
            "status": "running",
            "progress": 0,
            "logs": [],
            "error": None,
            "result": None,
            "type": job_type,
        }
    return job_id


def append_planner_job_log(job_id: str, message: str, progress: int | None = None) -> None:
    with PLANNER_JOBS_LOCK:
        job = PLANNER_JOBS.get(job_id)
        if not job:
            return
        job["logs"].append(message)
        if progress is not None:
            job["progress"] = progress


def complete_planner_job(job_id: str, result: dict) -> None:
    with PLANNER_JOBS_LOCK:
        job = PLANNER_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "completed"
        job["progress"] = 100
        job["result"] = result


def fail_planner_job(job_id: str, message: str) -> None:
    with PLANNER_JOBS_LOCK:
        job = PLANNER_JOBS.get(job_id)
        if not job:
            return
        job["status"] = "failed"
        job["error"] = message


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

    resp_json = resp.json()
    text = extract_output_text(resp_json)
    if not text:
        raise RuntimeError("Empty response from OpenAI")
    usage = resp_json.get("usage")
    logger.info(
        "OpenAI output model=%s output_len=%s usage=%s",
        OPENAI_MODEL,
        len(text),
        usage,
    )
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
    verse_ref: str,
    used: set[str],
    themes: list[str],
    color_mode: str,
    extra_prompt: str,
) -> str:
    themes_block = "\n".join(themes) if themes else "(themes unavailable)"
    used_block = "\n".join(sorted(used)) if used else "(none)"

    color_text = color_mode or "(not specified)"
    extra_block = extra_prompt.strip()
    extra_text = f"\n추가 요청:\n{extra_block}\n" if extra_block else ""
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
- 성경 구절 (ESV): {verse_ref or '(none)'}

프로젝트 정보:
- Themes list:\n{themes_block}
- Use the provided theme exactly.
- Avoid any verse references already used:\n{used_block}
- Do NOT recommend or return any verse from the used list.
- Size: {size} vertical.
- Color mode: {color_text}
- Translations: English = ESV, Korean = 개역개정
- verse_reference는 반드시 한글 책 이름 형식으로만 작성 (예: 히브리서 11:1). 쉼표/마침표 금지.
- verse_reference_en은 반드시 영문 책 이름 형식으로만 작성 (예: 2 Corinthians 5:7).
{extra_text}

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


def build_planner_story_prompt(
    theme: str,
    themes: list[str],
    used: set[str],
    verse_ref: str,
    color_mode: str,
    extra_prompt: str,
) -> str:
    series = "\n  1) The Ground Beneath(믿음) 2) Even So, Light(소망/위로) 3) Held Quietly(사랑)\n  4) The Gentle Joy(감사/기쁨) 5) Still Waters(평안/인도하심)\n  6) The Listening Room(기도/묵상) 7) Walk Bold(결단/용기/행동) 8) Known and Named(정체성/존재)"
    used_block = "\n".join(sorted(used)) if used else "(none)"
    extra_block = extra_prompt.strip()
    extra_text = f"\n- 내가 쓴 묵상 원문(또는 요약): {extra_block}\n" if extra_block else ""
    return f"""
SYSTEM
너는 네이버 블로그에 올릴 ‘성경 말씀 타이포그래피 포스터 제작기/기획기’ 전문 작가다.
글은 줄글 중심으로 자연스럽게 읽히되, 필요한 정보는 짧은 표로만 정리한다.
톤은 담담하고 과장 없으며, ‘내가 실제로 느낀 감정과 의도’를 중심으로 설명한다.
광고처럼 보이면 안 된다. 판매 유도 문구/가격/구매 링크는 넣지 않는다(요청 시에만).
모든 내용은 사용자가 제공한 묵상/포스터 정보를 기반으로 하고, 모르면 단정하지 말고 “내가 이렇게 느꼈다/의도했다”로 표현한다.

OUTPUT RULES (고정)
- 전체 길이: 대략 3000자 내외(지금 글 정도의 밀도)
- 구성: H1 1개 + 소제목 6~8개 정도
- 표는 2~3개 이하, 각 표는 2열(항목/내용) 또는 3열(구분/본문/비고)로 짧게
- 문단은 짧게(2~4문장), 호흡 빠르게
- 마지막에 해시태그 8~12개(주제/구절/시리즈/분위기 중심)
- 반드시 포함할 섹션:
  1) 도입(왜 이 구절이었는지/왜 포스터였는지)
  2) 8테마 중 어디인지 + 시리즈명
  3) 말씀(ESV + 개역개정) 표 1개
  4) 핵심 의미 ‘한 문장’ 요약
  5) 감정 포인트 3개(줄글로)
  6) 디자인 기획(타이포 위계, NOW 같은 장치, 흑백/컬러, 여백 의도)
  7) 제작 정보/의도 요약 표 1개(테마/앵커텍스트/키감정/디자인포인트/공간의도)
  8) 어울리는 공간/위하고 싶은 사람(줄글)
  9) 마무리(기획 의도/내가 남기고 싶은 결론)

USER (입력값)
[브랜드/프로젝트]
- 브랜드명: 고즈넉씨스튜디오(Gozneokssi Studio)
- 프로젝트명: LETTER FOR LIVING
- 시리즈(8테마): {series}

[이번 포스터 기본 정보]
- 구절 레퍼런스: {verse_ref or "(미정)"}
- ESV 본문: (구절 레퍼런스에 맞는 본문을 포함)
- 개역개정 본문: (구절 레퍼런스에 맞는 본문을 포함)
- 8테마/시리즈 선택: {theme}
- 포스터 핵심 장치: (본문에서 장치/위계를 자연스럽게 설명)
- 포스터를 만들며 의도한 키워드 5개: (본문에서 키워드로 정리)
- 이 포스터가 어울리는 공간 2~4곳: (본문에서 제안)
- 위하고 싶은 사람 2~4유형: (본문에서 제안)
- 앵커텍스트 후보 1~2개: (본문에서 제안)
- 제작도수(컬러): {color_mode or "(미지정)"}
- 이미 사용된 구절은 피한다: {used_block}
{extra_text}

[추가 제약]
- ‘제작기/기획기’ 느낌이 나게: 의도/선택/구조 중심으로
- 너무 신학 강의처럼 쓰지 말고, 내 감정과 기준을 담백히
- 구매 유도 금지(요청 시 제외)

이 입력값을 바탕으로, 위 OUTPUT RULES의 구조와 길이를 그대로 지켜 네이버 블로그용 글을 완성해줘.
""".strip()




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
            if has_hashtag_line(last_line):
                if not hashtags:
                    hash_pos = last_line.find("#")
                    prefix = last_line[:hash_pos].strip() if hash_pos >= 0 else ""
                    hashtags = last_line[hash_pos:].strip() if hash_pos >= 0 else last_line
                    if prefix:
                        lines[idx] = prefix
                    else:
                        lines = lines[:idx]
                elif last_line == hashtags:
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


def parse_blog_full_text(full_text: str) -> tuple[str, str, str]:
    lines = [line.rstrip() for line in full_text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    title = lines[0].strip() if lines else ""
    hashtags = ""
    if lines:
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx].strip()
            if not line:
                continue
            if re.search(r"#\\S+", line):
                hashtags = line
                lines = lines[:idx]
            break
    body_lines = lines[1:] if len(lines) > 1 else []
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)
    body = "\n".join(body_lines).strip()
    return title, body, hashtags


def strip_hashtag_line(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    idx = len(lines) - 1
    while idx >= 0 and not lines[idx].strip():
        idx -= 1
    if idx >= 0 and re.search(r"#\\S+", lines[idx].strip()):
        lines = lines[:idx]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def find_missing_sections(body: str) -> list[str]:
    required = ["배경", "의미", "묵상", "체크리스트", "되짚어볼 질문", "요약"]
    present: set[str] = set()
    for line in body.splitlines():
        normalized = line.strip()
        match = re.match(r"^(배경|의미|묵상|체크리스트|되짚어볼 질문|요약)\s*$", normalized)
        if match:
            present.add(match.group(1))
    return [section for section in required if section not in present]


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
        session.pop("last_planner_story", None)
    result = session.get("last_result")
    planner_story = session.get("last_planner_story", "")
    selected_theme = ""
    poster_sketch_variant = session.get("poster_sketch_variant", 0)

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
        if action == "regenerate_sketch":
            session["poster_sketch_variant"] = (poster_sketch_variant + 1) % 2
            session["preserve_planner_result"] = True
            return redirect(url_for("planner", notice="텍스트 스케치를 다시 생성했습니다."))

        theme = request.form.get("theme", "").strip()
        selected_theme = theme
        size_family = request.form.get("size_family", "").strip()
        size = request.form.get("size", "A2").strip()
        custom_size = request.form.get("custom_size", "").strip()
        color_mode = request.form.get("color_mode", "").strip()
        if not theme:
            error = "주제를 선택해 주세요."
        elif size_family == "custom" and not custom_size:
            error = "직접입력 사이즈를 입력해 주세요."
        if custom_size:
            size = custom_size
        extra_prompt = request.form.get("extra_prompt", "").strip()
        chosen_verse = ""

        if not error and theme not in themes:
            error = "주제를 8가지 중에서 선택해 주세요."
        if not error and not chosen_verse:
            chosen_verse = select_new_verse(theme, used)
            if not chosen_verse:
                error = "새로운 말씀을 찾지 못했습니다. 다시 시도해 주세요."
        if not error:
            try:
                verse_ref = chosen_verse
                theme_en, theme_ko = parse_theme(theme)
                result = {
                    "verse_reference": verse_ref,
                    "theme_display": selected_theme,
                    "theme_en": theme_en,
                    "theme_ko": theme_ko,
                }
                story_prompt = build_planner_story_prompt(
                    selected_theme,
                    themes,
                    used,
                    verse_ref,
                    color_mode,
                    extra_prompt,
                )
                story_text = call_openai_text(
                    story_prompt,
                    system_prompt="Follow the instructions exactly.",
                ).strip()

                BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

                if selected_theme in themes:
                    save_theme_override(THEME_MAP_PATH, verse_ref, selected_theme)

                theme_slug = slugify(theme_en)
                verse_slug = slugify(verse_ref.replace(":", "-"))
                date_tag = dt.date.today().strftime("%Y%m%d")
                base_name = f"{date_tag}_{theme_slug}_{verse_slug}"

                brief_path = BRIEFS_DIR / f"{base_name}_story.md"
                brief_path.write_text(story_text, encoding="utf-8")

                append_log(result, size, brief_path)
                session["last_result"] = result
                session["last_planner_story"] = story_text
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
        planner_story=planner_story,
        selected_theme=selected_theme,
        poster_sketch_variant=poster_sketch_variant,
    )


@app.post("/planner/start")
def planner_start():
    theme = request.form.get("theme", "").strip()
    size_family = request.form.get("size_family", "").strip()
    size = request.form.get("size", "A2").strip()
    custom_size = request.form.get("custom_size", "").strip()
    color_mode = request.form.get("color_mode", "").strip()
    extra_prompt = request.form.get("extra_prompt", "").strip()
    if not theme:
        return jsonify({"error": "주제를 선택해 주세요."}), 400
    job_id = init_planner_job("generate")
    thread = threading.Thread(
        target=run_planner_generation_job,
        kwargs={
            "job_id": job_id,
            "theme": theme,
            "size_family": size_family,
            "size": size,
            "custom_size": custom_size,
            "color_mode": color_mode,
            "extra_prompt": extra_prompt,
        },
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.post("/planner/sketch/start")
def planner_sketch_start():
    current_variant = session.get("poster_sketch_variant", 0)
    next_variant = (current_variant + 1) % 2
    job_id = init_planner_job("sketch")
    thread = threading.Thread(
        target=run_sketch_regeneration_job,
        kwargs={"job_id": job_id, "variant": next_variant},
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.get("/planner/status/<job_id>")
def planner_status(job_id: str):
    with PLANNER_JOBS_LOCK:
        job = PLANNER_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "작업을 찾을 수 없습니다."}), 404
        return jsonify(
            {
                "status": job["status"],
                "progress": job["progress"],
                "logs": job["logs"],
                "error": job["error"],
                "type": job.get("type"),
            }
        )


@app.post("/planner/finalize/<job_id>")
def planner_finalize(job_id: str):
    with PLANNER_JOBS_LOCK:
        job = PLANNER_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "작업을 찾을 수 없습니다."}), 404
    if job["status"] != "completed" or not job.get("result"):
        return jsonify({"error": "아직 완료되지 않았습니다."}), 400
    payload = job["result"]
    if job.get("type") == "generate":
        session["last_result"] = payload.get("result")
        session["last_planner_story"] = payload.get("story", "")
        session["preserve_planner_result"] = True
        session["flash_notice"] = "기획서가 생성되었습니다."
    elif job.get("type") == "sketch":
        session["poster_sketch_variant"] = payload.get("variant", 0)
        session["preserve_planner_result"] = True
        session["flash_notice"] = "텍스트 스케치를 다시 생성했습니다."
    with PLANNER_JOBS_LOCK:
        PLANNER_JOBS.pop(job_id, None)
    return jsonify({"ok": True})


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


@app.get("/blog/image")
def blog_image():
    raw = request.args.get("path", "").strip()
    if not raw:
        abort(404)
    target = Path(raw).expanduser()
    try:
        target = target.resolve()
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        abort(403)
    if not target.exists() or not target.is_file():
        abort(404)
    return send_file(target)


@app.post("/blog/start")
def start_blog_job():
    result = session.get("last_result")
    if not result:
        return jsonify({"error": "기획 생성 결과가 없습니다. 먼저 기획을 생성해 주세요."}), 400
    hashtags_count = int(request.form.get("hashtags_count", "7") or 7)
    site_link = request.form.get("site_link", "").strip()
    job_id = init_blog_job()
    thread = threading.Thread(
        target=run_blog_generation_job,
        kwargs={
            "job_id": job_id,
            "base_result": dict(result),
            "hashtags_count": hashtags_count,
            "site_link": site_link,
        },
        daemon=True,
    )
    thread.start()
    session["current_blog_job"] = job_id
    return jsonify({"job_id": job_id})


@app.get("/blog/status/<job_id>")
def blog_job_status(job_id: str):
    with BLOG_JOBS_LOCK:
        job = BLOG_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "작업을 찾을 수 없습니다."}), 404
        return jsonify(
            {
                "status": job["status"],
                "progress": job["progress"],
                "logs": job["logs"],
                "error": job["error"],
            }
        )


@app.post("/blog/images/start")
def start_image_job():
    draft_id = session.get("current_draft_id")
    image_prompt = session.get("last_image_prompt")
    blog_result = session.get("last_blog")
    if not blog_result:
        return jsonify({"error": "먼저 초안을 생성해 주세요."}), 400
    if not draft_id:
        return jsonify({"error": "초안 ID가 없습니다. 초안을 다시 생성해 주세요."}), 400
    if not isinstance(image_prompt, list) or not image_prompt:
        return jsonify({"error": "이미지 프롬프트가 없습니다. 초안을 다시 생성해 주세요."}), 400
    job_id = init_image_job()
    thread = threading.Thread(
        target=run_image_generation_job,
        kwargs={
            "job_id": job_id,
            "draft_id": str(draft_id),
            "image_prompt": image_prompt,
        },
        daemon=True,
    )
    thread.start()
    session["current_image_job"] = job_id
    session["preserve_blog_result"] = True
    return jsonify({"job_id": job_id})


@app.get("/blog/images/status/<job_id>")
def image_job_status(job_id: str):
    with IMAGE_JOBS_LOCK:
        job = IMAGE_JOBS.get(job_id)
        if not job:
            return jsonify({"error": "작업을 찾을 수 없습니다."}), 404
        return jsonify(
            {
                "status": job["status"],
                "progress": job["progress"],
                "logs": job["logs"],
                "error": job["error"],
            }
        )


@app.post("/blog/images/finalize/<job_id>")
def finalize_image_job(job_id: str):
    with IMAGE_JOBS_LOCK:
        job = IMAGE_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "작업을 찾을 수 없습니다."}), 404
    if job["status"] != "completed" or not job.get("result"):
        return jsonify({"error": "아직 완료되지 않았습니다."}), 400
    payload = job["result"]
    session["last_image_paths"] = payload.get("image_paths", [])
    session["preserve_blog_result"] = True
    session["flash_notice"] = "이미지를 생성했습니다."
    with IMAGE_JOBS_LOCK:
        IMAGE_JOBS.pop(job_id, None)
    return jsonify({"ok": True})


@app.post("/blog/finalize/<job_id>")
def finalize_blog_job(job_id: str):
    with BLOG_JOBS_LOCK:
        job = BLOG_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "작업을 찾을 수 없습니다."}), 404
    if job["status"] != "completed" or not job.get("result"):
        return jsonify({"error": "아직 완료되지 않았습니다."}), 400
    payload = job["result"]
    session["last_blog"] = payload.get("blog_result")
    session["last_image_prompt"] = payload.get("image_prompt")
    session["last_image_paths"] = payload.get("image_paths", [])
    session["current_draft_id"] = payload.get("draft_id")
    session["preserve_blog_result"] = True
    session["flash_notice"] = "초안을 생성했습니다."
    with BLOG_JOBS_LOCK:
        BLOG_JOBS.pop(job_id, None)
    return jsonify({"ok": True})


def run_blog_generation_job(
    job_id: str,
    base_result: dict,
    hashtags_count: int,
    site_link: str,
) -> None:
    try:
        append_blog_job_log(job_id, "말씀 구절을 준비합니다.", 5)
        prompt = build_blog_prompt(
            base_result,
            "",
            "",
            hashtags_count,
            site_link,
            "",
        )
        append_blog_job_log(job_id, "본문 생성을 시작합니다.", 15)
        blog_result = normalize_blog_result(call_openai(prompt))
        append_blog_job_log(job_id, "본문 생성이 완료되었습니다.", 55)

        draft_id = f"{dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{os.urandom(2).hex()}"
        theme = base_result.get("theme_display", "") or base_result.get("theme_en", "")
        verse = base_result.get("verse_reference", "")
        anchor = base_result.get("anchor_text", "")
        intent = base_result.get("one_line_intent", "")
        verse_en = base_result.get("verse_reference_en", "") or verse
        scripture_ko = base_result.get("korean_verse", "")
        scripture_en = base_result.get("english_verse", "")
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

        append_blog_job_log(job_id, "이미지는 필요 시 버튼으로 생성합니다.", 70)
        image_paths: list[str] = []

        append_blog_job_log(job_id, "작성 기록을 저장합니다.", 90)
        append_blog_log(blog_result, base_result)
        append_blog_job_log(job_id, "모든 작업이 완료되었습니다.", 100)

        complete_blog_job(
            job_id,
            {
                "blog_result": blog_result,
                "draft_id": draft_id,
                "image_prompt": image_prompt,
                "image_paths": image_paths,
            },
        )
    except Exception as exc:
        logger.exception("Blog draft generation failed")
        append_blog_job_log(job_id, f"실패: {exc}")
        fail_blog_job(job_id, str(exc))


def run_image_generation_job(job_id: str, draft_id: str, image_prompt: list[dict]) -> None:
    try:
        append_image_job_log(job_id, "이미지 생성 요청을 준비합니다.", 10)
        prompts = [item.get("text", "") for item in image_prompt if item.get("text")]
        if not prompts:
            raise RuntimeError("이미지 프롬프트가 비어 있습니다.")
        append_image_job_log(job_id, "이미지 생성 요청을 보냈습니다.", 30)
        images_dir = PROJECT_ROOT / "logs" / "blog-images"
        image_paths: list[str] = []
        total = len(prompts)
        for idx, prompt in enumerate(prompts, start=1):
            step_progress = 30 + int((idx - 1) / max(total, 1) * 50)
            append_image_job_log(job_id, f"{idx}/{total} 이미지 생성 중...", step_progress)
            generated_paths = generate_images(
                [prompt],
                images_dir,
                size="1024x1024",
                start_index=idx,
            )
            image_paths.extend([str(path) for path in generated_paths])
            append_image_job_log(job_id, f"{idx}/{total} 이미지 완료", 30 + int(idx / total * 50))
        blog_images = load_blog_images(BLOG_IMAGE_MAP_PATH)
        blog_images[str(draft_id)] = image_paths
        save_blog_images(BLOG_IMAGE_MAP_PATH, blog_images)
        append_image_job_log(job_id, "이미지 생성이 완료되었습니다.", 90)
        complete_image_job(job_id, {"image_paths": image_paths})
    except Exception as exc:
        logger.exception("Image generation failed")
        append_image_job_log(job_id, f"실패: {exc}")
        fail_image_job(job_id, str(exc))


def run_planner_generation_job(
    job_id: str,
    theme: str,
    size_family: str,
    size: str,
    custom_size: str,
    color_mode: str,
    extra_prompt: str,
) -> None:
    try:
        themes = read_themes(THEMES_PATH)
        used = read_used_verses(USED_VERSES_PATH)
        if theme not in themes:
            raise RuntimeError("주제를 8가지 중에서 선택해 주세요.")
        if size_family == "custom" and not custom_size:
            raise RuntimeError("직접입력 사이즈를 입력해 주세요.")
        if custom_size:
            size = custom_size
        append_planner_job_log(job_id, "말씀을 선택합니다.", 10)
        chosen_verse = select_new_verse(theme, used)
        if not chosen_verse:
            raise RuntimeError("새로운 말씀을 찾지 못했습니다. 다시 시도해 주세요.")
        append_planner_job_log(job_id, "제작기 문장을 생성합니다.", 40)
        story_prompt = build_planner_story_prompt(
            theme,
            themes,
            used,
            chosen_verse,
            color_mode,
            extra_prompt,
        )
        story_text = call_openai_text(
            story_prompt,
            system_prompt="Follow the instructions exactly.",
        )
        story_text = story_text.strip()
        verse_ref = chosen_verse
        theme_en, theme_ko = parse_theme(theme)
        result = {
            "verse_reference": verse_ref,
            "theme_display": theme,
            "theme_en": theme_en,
            "theme_ko": theme_ko,
        }
        BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
        if theme in themes:
            save_theme_override(THEME_MAP_PATH, verse_ref, theme)
        theme_slug = slugify(theme_en)
        verse_slug = slugify(verse_ref.replace(":", "-"))
        date_tag = dt.date.today().strftime("%Y%m%d")
        base_name = f"{date_tag}_{theme_slug}_{verse_slug}"
        brief_path = BRIEFS_DIR / f"{base_name}_story.md"
        brief_path.write_text(story_text, encoding="utf-8")
        append_log(result, size, brief_path)
        append_planner_job_log(job_id, "기획서가 생성되었습니다.", 100)
        complete_planner_job(job_id, {"result": result, "story": story_text})
    except Exception as exc:
        logger.exception("Planner generation failed")
        append_planner_job_log(job_id, f"실패: {exc}")
        fail_planner_job(job_id, str(exc))


def run_sketch_regeneration_job(job_id: str, variant: int) -> None:
    try:
        append_planner_job_log(job_id, "텍스트 스케치를 다시 그립니다.", 30)
        time.sleep(0.4)
        append_planner_job_log(job_id, "레이아웃을 정리합니다.", 70)
        time.sleep(0.3)
        complete_planner_job(job_id, {"variant": variant})
    except Exception as exc:
        logger.exception("Sketch regeneration failed")
        append_planner_job_log(job_id, f"실패: {exc}")
        fail_planner_job(job_id, str(exc))


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
    missing_sections = []
    incomplete_blog = False
    if blog_result:
        body_text = str(blog_result.get("body", "")).strip()
        missing_sections = find_missing_sections(body_text)
        if missing_sections:
            incomplete_blog = True
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
        if action == "update_blog_result":
            if not blog_result:
                session["flash_error"] = "수정할 초안이 없습니다. 먼저 생성해 주세요."
                return redirect(url_for("blog"))
            full_text = request.form.get("blog_body_full", "").strip()
            updated_title, updated_body, updated_hashtags = parse_blog_full_text(full_text)
            blog_result["title"] = updated_title
            blog_result["body"] = updated_body
            blog_result["hashtags"] = updated_hashtags
            session["last_blog"] = blog_result
            session["preserve_blog_result"] = True
            session["flash_notice"] = "초안을 수정했습니다."
            return redirect(url_for("blog"))
        if action == "continue_blog_result":
            if not blog_result:
                session["flash_error"] = "추가 생성할 초안이 없습니다. 먼저 생성해 주세요."
                return redirect(url_for("blog"))
            full_text = request.form.get("blog_body_full", "").strip()
            updated_title, updated_body, updated_hashtags = parse_blog_full_text(full_text)
            if not updated_body:
                session["flash_error"] = "본문이 비어 있어 추가 생성할 수 없습니다."
                return redirect(url_for("blog"))
            missing_sections = find_missing_sections(updated_body)
            missing_hint = ", ".join(missing_sections) if missing_sections else "없음"
            prompt = f"""
너는 신앙 묵상 블로그 글을 이어서 작성하는 편집자다.
아래 본문 뒤에 이어질 내용만 작성하라. 이미 쓴 문장은 반복하지 않는다.
제목/해시태그는 절대 작성하지 않는다. 소제목은 단독 줄로 작성한다.

누락된 소제목: {missing_hint}

이미 작성된 본문:
{updated_body}
""".strip()
            try:
                append_text = call_openai_text(
                    prompt,
                    system_prompt="You only return the continuation text.",
                )
                append_text = strip_hashtag_line(append_text)
                if append_text:
                    updated_body = f"{updated_body}\n\n{append_text}".strip()
                blog_result["title"] = updated_title
                blog_result["body"] = updated_body
                blog_result["hashtags"] = updated_hashtags
                session["last_blog"] = blog_result
                session["preserve_blog_result"] = True
                session["flash_notice"] = "본문을 이어서 생성했습니다."
            except Exception as exc:
                logger.exception("Blog continuation failed")
                session["flash_error"] = f"추가 생성 실패: {exc}"
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
        missing_sections=missing_sections,
        incomplete_blog=incomplete_blog,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(debug=True, port=port)
