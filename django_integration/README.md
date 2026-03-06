# Django Integration — OpenWhispr Push-to-Talk

A minimal Django application that demonstrates how to embed programmatic
push-to-talk speech dictation into a web page using the OpenWhispr
companion service.

## Prerequisites

1. **Companion service running** — see `companion/README.md`
2. Python 3.10+

## Quick start

```bash
cd django_integration/
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cd dictation_project/
python manage.py migrate        # creates the SQLite file (no schema needed for this demo)
python manage.py runserver
```

Open http://localhost:8000 — you should see the dictation UI.

Make sure the companion service is also running:

```bash
# In a separate terminal, from the repo root:
cd companion/
source .venv/bin/activate
uvicorn companion.server:app --host 127.0.0.1 --port 8765
```

## How it works

```
User clicks "Start Dictation"
    │
    ▼
Browser  POST http://localhost:8765/start
    │
    ▼                       (or WebSocket /ws — toggle in the UI)
Companion service starts microphone capture
    │
User speaks
    │
User clicks "Stop"
    │
    ▼
Browser  POST http://localhost:8765/stop
    │
    ▼
Companion stops recording, runs faster-whisper, returns JSON
    {"status": "done", "transcript": "Hello world", "duration_seconds": 2.1}
    │
    ▼
JavaScript inserts transcript into <textarea>
```

No clipboard. No keyboard simulation. No OS automation.

## Files

```
django_integration/
  requirements.txt                   Django dependency
  README.md                          This file

  dictation_project/
    manage.py
    dictation_project/
      settings.py                    Minimal Django settings
      urls.py
      wsgi.py
    dictation/
      apps.py
      urls.py
      views.py                       Single view — renders template
      templates/dictation/
        dictation.html               Full dictation UI with JS client
```

## Customising the companion URL

By default the template contacts `http://127.0.0.1:8765`.  Change this via:

```python
# dictation_project/settings.py
COMPANION_URL = "http://127.0.0.1:8765"
```

or set the `COMPANION_URL` environment variable before starting Django.

## Transport modes

The UI offers two transport options:

| Mode | Description | Best for |
|------|-------------|----------|
| HTTP | POST /start then POST /stop | Simple, works everywhere |
| WebSocket | Persistent `/ws` connection | Lower perceived latency, real-time events |

Switch between them using the radio buttons at the top of the dictation card.

## Integrating into an existing Django project

1. Copy the `dictation/` app directory into your project.
2. Add `"dictation"` to `INSTALLED_APPS`.
3. Include `dictation.urls` in your root `urls.py`.
4. Set `COMPANION_URL` in settings.
5. Start the companion service alongside your Django server.

The template is self-contained (no external CDN dependencies) and can be
embedded into any existing base template by extending it.
