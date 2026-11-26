# VayuMitra — Urban Air Quality Assistant

A production-oriented Flask application for live air-quality context, indoor protection guidance, emergency screening, AI chat, and purifier sizing.

## Features

- Live modeled air-quality retrieval via Open-Meteo; source, location grid, timestamp, scale, pollutants, and limitations are displayed.
- Regional identity: VayuMitra in India, Pakistan, Bangladesh, Nepal, and Sri Lanka; CleanAir Companion elsewhere.
- Immediate CO and respiratory emergency responses.
- Purifier calculator for m³ and ft dimensions, and m³/h, m³/min, or CFM CADR.
- Secure server-side AI integration through `OPENAI_API_KEY` (OpenAI-compatible APIs supported).
- Pooled HTTP session with timeouts/retry, plus 10-minute TTL caching of Open-Meteo air-quality lookups (keyed by rounded coordinates) and 24-hour geocode caching.
- Flask session-based chat history, validation, loading/error states, and responsive UI.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Open `http://localhost:5000`. The dev server binds to `127.0.0.1`; set `HOST=0.0.0.0` to expose it.

Set `SECRET_KEY` to a long random value. Optional `OPENAI_API_KEY` enables live model responses; without it the app uses safe local fallback guidance.

## Testing

```powershell
python -m pytest -q
```

## Deploy

1. Set `SECRET_KEY`, `OPENAI_API_KEY` only if used, and `PORT` in platform environment variables.
2. Never commit `.env` or place API keys in JavaScript.
3. Use a production WSGI server, for example:

```powershell
pip install gunicorn
gunicorn -b 0.0.0.0:8000 app:app
```

The Open-Meteo model endpoint is a measured or modeled grid result as returned by that provider, not a guaranteed neighborhood regulatory reading. Users should check the nearest official station or authority alert.

## Data limitation

Open-Meteo does not establish the nearest physical monitoring station. The UI explicitly identifies the output as modeled grid data. Live data failures are shown rather than fabricated.
