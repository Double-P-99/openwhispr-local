# OpenWhispr Integration Technical Evaluation

**Date:** March 2026  
**Repository evaluated:** [openwhispr-local](https://github.com/Double-P-99/openwhispr-local)  
**Purpose:** Determine viability of integrating OpenWhispr push-to-talk dictation into a product, with Django or Kotlin Multiplatform as the integration host.

---

## 1. Executive Summary

OpenWhispr is an actively maintained Electron desktop application that bundles `whisper.cpp` (and optionally NVIDIA Parakeet via `sherpa-onnx`) as local speech-to-text engines, with OpenAI Whisper API as a cloud fallback. The app is **not** a reusable library or embeddable SDK — it is a desktop companion with global hotkeys and system-wide clipboard/paste behaviour.

**However**, the internal architecture contains a useful escape hatch: a bundled `whisper-server` binary that exposes a **local HTTP REST API** (ports 8178–8199) and a **WebSocket Parakeet server** (ports 6006–6029) while the app is running. These endpoints are OpenAI-Whisper-API-compatible, meaning any HTTP client — including Django — can POST audio files to them and receive transcription text.

**Recommendation:** Use **Django + local WebSocket bridge** (Path 2). Run the `whisper-server` binary (or OpenWhispr itself) as a desktop companion. Capture audio in the browser with the MediaRecorder API, proxy it through a lightweight Django view, and return the transcription via Django Channels WebSocket. This is the fastest, most robust path. Direct Django integration alone (Path 1) is brittle because it cannot access the microphone server-side. Kotlin Multiplatform is unnecessary overhead for this use case.

---

## 2. What OpenWhispr Is

OpenWhispr is a cross-platform (macOS / Windows / Linux) desktop dictation application built with:

| Layer | Technology |
|---|---|
| Shell | Electron 36 + Node.js |
| UI | React 19 + TypeScript + Tailwind CSS v4 |
| STT (local) | whisper.cpp HTTP server + NVIDIA Parakeet (sherpa-onnx) |
| STT (cloud) | OpenAI Whisper API |
| Paste | AppleScript (macOS), PowerShell / nircmd (Windows), XTest / xdotool (Linux) |
| Database | better-sqlite3 (transcription history) |

The primary UX is: user presses a global hotkey → audio is captured via `MediaRecorder` in the Electron renderer → audio blob is sent over Electron IPC → written to a temp file → `whisper-server` HTTP endpoint transcribes it → text is pasted into whatever app has focus via OS-level paste mechanism.

---

## 3. How It Works at a High Level

```
┌─────────────────────────────────────────────────────────┐
│  Electron App (OpenWhispr)                               │
│                                                          │
│  Renderer (React)                                        │
│    └─ MediaRecorder → audio blob                         │
│         │                                                │
│         ▼  (Electron IPC)                                │
│  Main Process                                            │
│    └─ writes temp WAV file                               │
│         │                                                │
│         ▼  (HTTP POST multipart)                         │
│  whisper-server (localhost:8178-8199)                    │
│    └─ returns JSON { text: "transcription" }             │
│         │                                                │
│         ▼                                                │
│  Clipboard + OS paste into active window                 │
└─────────────────────────────────────────────────────────┘
```

Key insight: **the `whisper-server` binary is a standalone HTTP process** that can receive audio and return text entirely independently of the Electron UI. It is started by the app but listens on `127.0.0.1` and can be targeted by any local HTTP client.

---

## 4. Hardware Requirements

The following table is based on the model registry (`src/models/modelRegistryData.json`) and empirical whisper.cpp benchmarks on CPU-only hardware. GPU acceleration (CUDA) is supported by whisper.cpp and is detected/used automatically when available via the `-cuda` variant binary.

| Model | Disk | Peak RAM (CPU) | ~Latency (30 s audio, modern 8-core) | ~Latency (30 s audio, entry 4-core) | Quality | Recommended use case |
|---|---|---|---|---|---|---|
| **tiny** | 75 MB | ~600 MB | ~1 s | ~2–3 s | ★★☆☆☆ | Drafts, quick notes |
| **base** | 142 MB | ~900 MB | ~2 s | ~4–6 s | ★★★☆☆ | General dictation (recommended) |
| **small** | 466 MB | ~2 GB | ~5 s | ~10–15 s | ★★★★☆ | Professional use, multi-language |
| **medium** | 1.5 GB | ~5 GB | ~12 s | ~30+ s | ★★★★☆ | High-accuracy, background tasks |
| **large** | 3 GB | ~10 GB | ~25 s | impractical | ★★★★★ | Batch / server-side, CUDA needed |
| **turbo** | 1.6 GB | ~5 GB | ~6 s | ~15 s | ★★★★★ | Best CPU tradeoff for quality+speed |
| **Parakeet 0.6B** | 680 MB | ~2 GB | ~1–2 s | ~3–5 s | ★★★★☆ | English-only, best real-time |

**CPU-only usability:** `tiny`, `base`, and `small` are usable on a 4-core laptop. `turbo` is the best quality-per-CPU-second on an 8-core machine. `large` requires GPU for real-time use.

**GPU acceleration:** whisper.cpp ships CUDA variants (`whisper-server-linux-x64-cuda`). Enabling CUDA is automatic when detected. A mid-range GPU (RTX 3060, 8 GB VRAM) reduces `large`-model latency from 25 s to ~2 s.

**Minimum viable machine profile:**
- 8 GB RAM, 4-core CPU, 2 GB disk free → `base` or `small` comfortably  
- 16 GB RAM, 8-core CPU → `turbo` usable  
- Any machine with CUDA GPU → `large` viable in real-time

---

## 5. Integration Options

### 5.1 Path 1 — Pure Django / Browser Integration

**Can a browser button trigger dictation and get text into a Django form field?**

**Assessment: Achievable but requires a desktop companion running.**

The browser cannot directly call the whisper-server (CORS blocks cross-origin localhost calls from a web origin, and the browser cannot spawn processes). The integration path is:

1. Browser captures audio via `MediaRecorder` (no installation required)
2. Audio blob sent to Django backend via `fetch` (multipart POST)
3. Django backend POSTs audio to `whisper-server` on `localhost:8178-8199`
4. Django returns transcription JSON to browser
5. JavaScript inserts text into the target `<textarea>`

**Why it's not "pure" Django:** whisper-server must already be running locally on the user's machine (either via the full OpenWhispr app or the standalone `whisper-server` binary). This is a **desktop companion dependency**, not a server-side cloud service. Django itself just acts as a thin proxy.

**Is this fragile?** If the whisper-server binary is run as a **managed daemon** (auto-start on login, `systemd`, `launchd`, or Windows service), this is actually solid. If users must remember to open OpenWhispr before using the Django form, that is fragile.

**Clipboard-only fallback:** Without the server, the only alternative is OpenWhispr's native hotkey + paste behaviour, which is completely outside the browser's control and cannot reliably target a specific Django form field.

### 5.2 Path 2 — Django + Local WebSocket Bridge (Recommended)

A lightweight helper process (`local-helper/whisper_bridge.py`) runs on the user's machine and:
- Manages the `whisper-server` lifecycle (starts it if not running)
- Exposes a local WebSocket at `ws://127.0.0.1:9876`
- Accepts audio blobs from the browser (or via Django Channels proxy)
- Returns transcription text in real time

The Django app uses Channels or a plain `EventSource` / `fetch` to coordinate. This is the cleanest architecture because:
- **No port-discovery fragility**: the bridge always listens on a known port
- **Audio goes browser → bridge directly**: eliminates extra Django proxy hop for audio data
- **Model lifecycle managed**: bridge starts/stops whisper-server as needed
- **Works offline**: all processing local, no cloud needed

See `integration/local-helper/` for a full working prototype.

### 5.3 Path 3 — Kotlin Multiplatform

**Assessment: Not recommended.**

KMP could host a JVM wrapper around the `whisper-server` process and expose a native UI. However:
- The `whisper-server` binary is already cross-platform; KMP adds no value there
- The UI is already React (Electron). A KMP desktop app would duplicate that effort
- KMP mobile (Android/iOS) cannot use the same `whisper.cpp` GGML binaries without additional build infrastructure (Android NDK, iOS xcframework)
- KMP does not make it materially easier to interface with Django compared to a plain Python bridge script
- The use case (push-to-talk in a Django web form) is a web problem, not a native app problem

**Only recommend KMP if:** you need a first-class cross-platform native desktop app as the primary product, not as a companion to a web app. For this use case, a 50-line Python bridge script is orders of magnitude simpler.

---

## 6. Functional Validation Notes

### What whisper.cpp handles well
- English dictation: excellent with `base` and above
- Spanish, French, German, Portuguese: good with `small` and above
- Punctuation: reasonable auto-punctuation, no guarantees on exact formatting
- Long-form (2–5 minutes): works, latency scales linearly with audio length
- Domain vocabulary: improved via the `prompt` parameter (custom dictionary feature in OpenWhispr)

### Known limitations
- Noisy environments: quality degrades below ~15 dB SNR; `small` or `medium` required
- Mixed-language within one recording: unreliable — pick one language per session
- Real-time streaming: whisper.cpp is batch-only; Parakeet via WebSocket supports near-real-time
- Text insertion into web forms: requires JavaScript in the page; the OS-paste approach used by OpenWhispr's Electron integration is not usable from a browser security context
- Latency on `base` model: ~2–4 s end-to-end (audio capture → transcription → DOM insertion)

---

## 7. Risks and Blockers

| Risk | Severity | Mitigation |
|---|---|---|
| whisper-server must be running locally | High | Ship a companion installer; use auto-start daemon |
| Port discovery (8178–8199 range) | Medium | Fix bridge to known port 9876; bridge discovers whisper-server internally |
| Browser mic permissions | Low | Standard `getUserMedia` — handled by browser |
| CORS / CSP blocking localhost calls | Medium | Proxy through Django backend or configure CORS headers on bridge |
| Model cold-start delay (2–5 s first request) | Medium | Pre-warm server on login (already done in OpenWhispr) |
| Electron app not installed by user | High | Offer standalone `whisper-server` binary install as lighter alternative |
| Windows: port may be blocked by firewall | Low | Bridge binds 127.0.0.1 only; Windows Defender allows loopback by default |
| Parakeet WebSocket API not stable/documented | Medium | Use whisper-server HTTP API instead (more stable) |

---

## 8. Final Recommendation

### Fastest path
**Django + local WebSocket bridge** using `whisper-server` binary directly (no full Electron app required). Ship the bridge as a small Python script or compiled binary that auto-starts with the OS. Django handles the web form; the bridge handles microphone → transcription → WebSocket push to browser.

### Safest path
**Same architecture**, but use OpenAI Whisper API as fallback when the local server is unavailable. This removes the desktop companion requirement for users who don't need privacy-first local processing.

### Most maintainable path
**Same Django + bridge architecture**, but package the bridge as a pip-installable service with a `systemd` / `launchd` / Windows Service wrapper. Version-pin the `whisper-server` binary alongside the Django app's deployment. Use the OpenAI-compatible API contract (`multipart/form-data` POST) as the stable interface so you can swap backends (OpenWhispr → faster-whisper → cloud) without changing Django code.

### Do not use
- **Clipboard/hotkey-only**: not deterministic for web form injection
- **Kotlin Multiplatform**: wrong tool for this problem
- **Full Electron app as hard dependency**: too heavy; standalone `whisper-server` binary is sufficient

---

## 9. Code and Prototype Files

See the `integration/` directory:

```
integration/
├── django-prototype/          # Full Django proof-of-concept
│   ├── manage.py
│   ├── requirements.txt
│   ├── dictation_project/
│   └── dictation_demo/
│       ├── views.py           # Proxy view + WebSocket consumer
│       ├── urls.py
│       ├── consumers.py       # Django Channels WebSocket consumer
│       └── templates/
│           └── dictation_demo/index.html
├── local-helper/
│   ├── whisper_bridge.py      # Standalone WebSocket bridge
│   ├── requirements.txt
│   └── README.md
└── kotlin-multiplatform/
    └── FEASIBILITY_NOTES.md
```

---

## 10. Next Steps

1. **Decision:** Confirm Django + local bridge is the path.
2. **Install:** Run `integration/local-helper/whisper_bridge.py` on a developer machine alongside a downloaded `whisper-server` binary.
3. **Test:** Run the Django prototype against a live whisper-server; complete the test plan in `TEST_PLAN.md`.
4. **Package:** Decide on bridge distribution (Python script vs. compiled binary vs. included in OpenWhispr installer).
5. **Production hardening:** Add TLS to the bridge (or restrict to localhost-only); add auth token to prevent other local processes from submitting audio.
6. **CI:** Add a GitHub Actions workflow that runs the Django unit tests on every PR.
