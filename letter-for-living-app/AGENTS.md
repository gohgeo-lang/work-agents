# Repository Guidelines

## Project Structure & Module Organization
- `app.py` is the Flask entrypoint and routing hub for planner, blog, and settings pages.
- `agents/` holds focused logic (prompt building, Selenium uploader, image helpers).
- `templates/` contains Jinja views: `home.html`, `index.html` (planner), `blog.html`,
  `settings.html`, and `brief.html`.
- `static/styles.css` holds shared UI styling.
- Runtime output is written outside the repo to `LFL_PROJECT_ROOT` (default:
  `/Users/admin/Desktop/고즈넉씨스튜디오/letter-for-living/`). Key subpaths:
  `briefs/`, `logs/posters-log.csv`, `logs/blog-log.csv`, `logs/settings.json`,
  `logs/generated-images/`.

## Build, Test, and Development Commands
- `python3 -m venv .venv` and `source .venv/bin/activate` set up a local venv.
- `pip install -r requirements.txt` installs Flask, requests, and Selenium.
- `python app.py` starts the server at `http://127.0.0.1:5000`.

## Configuration & Runtime Notes
- Create `.env` with `OPENAI_API_KEY`. Optional: `OPENAI_MODEL`,
  `LFL_PROJECT_ROOT`, `LFL_USED_VERSES`, `LFL_THEMES`, `FLASK_SECRET_KEY`.
- The app calls the OpenAI Responses API; local runs need network access.
- Selenium-based Naver automation uses Chrome and writes a driver log to
  `logs/chromedriver.log` under `LFL_PROJECT_ROOT`.

## Coding Style & Naming Conventions
- Python: follow PEP 8 (4-space indentation, explicit names).
- Templates/CSS: keep selectors descriptive and kebab-case.
- Prefer small, single-purpose functions with clear data flow.

## Testing Guidelines
- No automated tests are present. If you add tests, document the framework and
  place them under `tests/`.

## Commit & Pull Request Guidelines
- Git history is minimal, so no enforced convention exists.
- Use concise, scoped commit messages (example: `feat: add blog history log`).
- PRs should include a summary, verification steps, and UI screenshots if
  layouts change.
