def build_shorts_prompt(
    result: dict,
    tone: str,
    length_seconds: str,
    cuts_count: int,
    extra_prompt: str,
) -> str:
    verse = result.get("verse_reference") or ""
    korean_verse = result.get("korean_verse") or ""
    english_verse = result.get("english_verse") or ""

    extra = extra_prompt.strip()
    extra_block = f"\n추가 요청:\n{extra}\n" if extra else ""

    return f"""
너는 성경 말씀을 직접 설교하지 않고,
사람의 보편적인 상태와 질문을 통해
조용한 울림을 만드는 숏폼 영상 감독이자 작가다.

이 숏츠의 목표는
기독교인이 아닌 사람도
종교 콘텐츠라는 거부감 없이
끝까지 보게 만드는 것이다.

⚠️ 반드시 지켜야 할 원칙:

1. 설명하지 않는다.
2. 가르치지 않는다.
3. 하나님, 예수, 신앙, 교회라는 단어를
   본문(제목/스크립트/자막/나레이션)에 직접 사용하지 않는다.
4. 질문 → 여백 → 말씀의 흐름을 따른다.
5. 감정은 과장하지 않고 담담하게 유지한다.
6. 정답을 제시하지 않는다.
7. 성경 구절은 영상 후반부에만 조용히 등장시킨다.
8. 성경 배경 설명이나 인물 소개는 절대 하지 않는다.

────────────────────

[입력 말씀]
- 성경 구절: {verse}
- 본문(개역개정 or ESV): {korean_verse or english_verse}

────────────────────

[출력 결과는 아래 형식을 정확히 따른다]

1. Hook (0–3초)
- 일상적인 질문 1문장
- 최대 2줄
- 보편적인 삶의 상황으로 시작

2. Everyday Scene (3–15초)
- 현대인의 일상 장면을 묘사
- 누구나 겪을 법한 상황
- 성경, 종교적 표현 금지

3. Pause (15–25초)
- 판단하지 않는 질문 또는 여백 문장
- 해답 제시 금지
- 시청자가 스스로 생각하게 만드는 문장

4. Verse (25–35초)
- 입력된 성경 구절을 그대로 인용
- 설명 없이 구절만 제시
- 마지막 줄에 성경 출처만 표기

5. Closing Meditation (선택, 35–45초)
- 명령형이 아닌 묵상 한 줄
- “~해야 한다” 금지
- 조용히 남는 문장

전체 톤은
차분하고 절제되며
시처럼 읽히되
의미를 밀어붙이지 않는다.

{extra_block}

반드시 JSON으로만 응답. 아래 구조를 유지:
{{
  "title": "",
  "hook": "",
  "everyday_scene": "",
  "pause": "",
  "verse": "",
  "closing_meditation": "",
  "script": "",
  "description": "",
  "cuts": [
    {{
      "cut": 1,
      "visual": "",
      "mood": "",
      "motion": "",
      "on_screen": "",
      "timing": ""
    }}
  ],
  "image_prompts": [""]
}}

script는 hook, everyday_scene, pause, verse, closing_meditation 순서로
줄바꿈하여 합친 전체 나레이션 텍스트로 작성한다.
""".strip()
