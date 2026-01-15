# Letter for Living Planner (Local Web App)

## Setup

1) Create `.env` from the example and add your OpenAI key.

```
cp .env.example .env
```

2) Install dependencies.

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Run the app.

```
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

## What it does

- Generates a poster plan with ESV + 개역개정 text
- Produces an ASCII sketch
- Saves briefs and sketches in the project folder
- Logs each poster in `logs/posters-log.csv`
- Updates the used verse list to avoid duplicates

## Notes

- If the ESV/개역개정 text must be exact, consider pasting the verse text manually.
- The app uses the OpenAI Responses API and requires network access.
