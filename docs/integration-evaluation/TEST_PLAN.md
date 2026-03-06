# OpenWhispr Integration — Reproducible Test Plan

**Version:** 1.0  
**Target:** `whisper-server` HTTP API (OpenWhispr local helper or standalone binary)  
**Integration under test:** Django prototype + local WebSocket bridge  

---

## 1. Installation Steps

### 1.1 Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | For Django prototype and bridge |
| pip | latest | `python -m pip install --upgrade pip` |
| Node.js (optional) | ≥ 20 | Only if running full Electron OpenWhispr app |
| `whisper-server` binary | latest | From OpenWhispr GitHub releases or standalone |
| `ffmpeg` | ≥ 5.0 | Required by whisper-server for audio conversion |
| 4 GB free disk | — | For `base`+`small` models |
| Microphone | any | USB or built-in |

### 1.2 Get the whisper-server binary

**Option A — From OpenWhispr app (already installed)**

The binary is at:
```
# macOS
~/Library/Application Support/openwhispr/app.asar.unpacked/...
# Windows
%APPDATA%\openwhispr\...
# Linux
~/.config/openwhispr/...
```

Or find it via: `find ~/.config/openwhispr -name "whisper-server*" 2>/dev/null`

**Option B — Standalone whisper.cpp build**

```bash
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
cmake -B build -DWHISPER_BUILD_SERVER=1
cmake --build build --config Release
# binary: build/bin/whisper-server
```

**Option C — From OpenWhispr releases**

```bash
# Check latest release at:
# https://github.com/openwhispr/openwhispr/releases
# Download the appropriate whisper-cpp-{platform}-{arch} binary
```

### 1.3 Download a GGML model

```bash
# tiny (fastest, for baseline testing)
wget https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin -O /tmp/ggml-tiny.bin

# base (recommended for real testing)
wget https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin -O /tmp/ggml-base.bin

# small (for quality testing)
wget https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin -O /tmp/ggml-small.bin
```

### 1.4 Start the whisper-server manually

```bash
./whisper-server --model /tmp/ggml-base.bin --host 127.0.0.1 --port 8178
# Verify it started:
curl http://127.0.0.1:8178/health
# Expected: {"status":"ok"}
```

### 1.5 Set up the local bridge

```bash
cd integration/local-helper
pip install -r requirements.txt
python whisper_bridge.py --whisper-url http://127.0.0.1:8178 --port 9876
```

### 1.6 Set up the Django prototype

```bash
cd integration/django-prototype
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 8000
# Open: http://localhost:8000/
```

---

## 2. Test Matrix

### 2.1 Transcription Quality Tests

Run each audio scenario against `base`, `small`, and `turbo` models.

| ID | Scenario | Input audio | Expected result | Pass criteria |
|---|---|---|---|---|
| TQ-01 | English, clear, short | 5 s: "The quick brown fox jumps over the lazy dog." | Exact or near-exact match | WER ≤ 5% |
| TQ-02 | English, clear, long | 60 s technical paragraph | Coherent, correct words | WER ≤ 10% |
| TQ-03 | Spanish | 10 s: "Buenos días, ¿cómo estás hoy?" | Correct Spanish text | WER ≤ 10% |
| TQ-04 | French | 10 s: "Bonjour, comment vous appelez-vous?" | Correct French text | WER ≤ 10% |
| TQ-05 | Mixed English+Spanish | "I need to hacer una reunión mañana" | Both language segments correct | WER ≤ 20% |
| TQ-06 | Punctuation | Read a sentence with pauses between clauses | Commas and periods inserted | Punctuation correct ≥ 60% |
| TQ-07 | Noisy environment | Same as TQ-01 with 60 dB background noise | Recognisable output | WER ≤ 25% |
| TQ-08 | Domain vocabulary | Medical: "The patient has hypertension and bradycardia" | Correct medical terms | WER ≤ 5% |
| TQ-09 | Domain vocabulary + custom dict | Same as TQ-08 with custom dictionary enabled | Improved vs TQ-08 | WER ≤ TQ-08 |
| TQ-10 | Whisper (base) vs Parakeet (0.6B) | TQ-01 audio | Both produce same text | Same WER ± 5% |

**WER (Word Error Rate) calculation:**
```python
def wer(reference, hypothesis):
    r = reference.lower().split()
    h = hypothesis.lower().split()
    # Levenshtein distance / len(reference)
    ...
```

### 2.2 Latency Tests

Measure end-to-end latency from audio-capture-stop to text-in-DOM for the Django prototype.

| ID | Audio length | Model | Expected latency | Pass criteria |
|---|---|---|---|---|
| LA-01 | 5 s | tiny | < 1 s | ≤ 1 s p95 |
| LA-02 | 5 s | base | < 3 s | ≤ 3 s p95 |
| LA-03 | 5 s | small | < 6 s | ≤ 6 s p95 |
| LA-04 | 30 s | base | < 8 s | ≤ 8 s p95 |
| LA-05 | 30 s | turbo | < 10 s | ≤ 10 s p95 |
| LA-06 | Cold-start (first request) | base | < 8 s | ≤ 8 s |
| LA-07 | Warm (subsequent request) | base | < 3 s | ≤ 3 s p95 |

**Latency measurement script:** `integration/local-helper/bench_latency.py`

### 2.3 Integration / Functional Tests

| ID | Test | Steps | Pass criteria |
|---|---|---|---|
| IN-01 | Browser button starts capture | Click "Start Dictation" | Mic indicator appears; MediaRecorder active |
| IN-02 | Transcription fills textarea | Speak; click "Stop Dictation" | Text appears in `<textarea>` within LA-02 latency |
| IN-03 | Correct field targeting | Page has 3 textareas; click button next to #2 | Text inserted into textarea #2 only |
| IN-04 | Stop before speaking | Click start, then stop immediately | Empty or minimal text; no crash |
| IN-05 | Error when bridge is down | Kill whisper_bridge.py; try dictation | Friendly error message shown; no unhandled exception |
| IN-06 | Long recording (120 s) | Speak for 2 minutes | Transcription returned; no timeout |
| IN-07 | Multiple sequential recordings | 3 recordings back-to-back | Each transcription is distinct and correct |
| IN-08 | Simultaneous users (2 tabs) | Open 2 browser tabs; start dictation in both | Both complete independently; no cross-contamination |
| IN-09 | WebSocket reconnect | Drop bridge; reconnect; dictate | Transcription succeeds after reconnect |
| IN-10 | CSRF protection | POST to /api/transcribe/ without CSRF token | 403 response |
| IN-11 | Audio size limit | Send 15 MB audio blob | Reject with 413 or sensible error |

### 2.4 Platform Tests

| ID | Platform | Python | Test IDs to run |
|---|---|---|---|
| PL-01 | Ubuntu 22.04 | 3.11 | TQ-01, LA-02, IN-01, IN-02, IN-05 |
| PL-02 | macOS 14 (arm64) | 3.12 | TQ-01, LA-02, IN-01, IN-02, IN-05 |
| PL-03 | Windows 11 | 3.11 | TQ-01, LA-02, IN-01, IN-02, IN-05 |
| PL-04 | Ubuntu + CUDA GPU | 3.11 | LA-04, LA-05 (verify GPU path faster) |

---

## 3. Success / Failure Criteria

### 3.1 Go / No-Go Gates

| Gate | Requirement | Consequence of failure |
|---|---|---|
| G-01 | TQ-01 WER ≤ 5% on `base` | Do not proceed; check model/binary |
| G-02 | LA-02 p95 ≤ 3 s | Consider `tiny` model only |
| G-03 | IN-02 passes on all 3 platforms | Architecture invalid; revisit bridge |
| G-04 | IN-05 shows error (no crash) | Fix error handling before shipping |
| G-05 | IN-10 returns 403 | Fix CSRF; do not ship without this |

### 3.2 Acceptable Quality Thresholds

- **WER ≤ 5%** for English `base`: ship
- **WER 5–15%**: acceptable for drafts; recommend `small` or `turbo`
- **WER > 15%**: unacceptable; investigate audio quality, sample rate, or model size

### 3.3 Unacceptable Outcomes

- Any unhandled exception in the Django view → fix before ship
- Text inserted into wrong form field → fix JavaScript targeting logic
- Transcription leaking between concurrent users → critical bug
- Bridge process crashing on large audio → fix timeout/buffer handling

---

## 4. Performance Measurements

### 4.1 Benchmark Procedure

```bash
# 1. Generate test audio files (requires sox or ffmpeg)
ffmpeg -f lavfi -i "sine=frequency=440:duration=5" -ar 16000 -ac 1 /tmp/test_5s.wav
# Or use real speech recordings

# 2. Run latency benchmark
cd integration/local-helper
python bench_latency.py \
  --audio /tmp/test_5s.wav \
  --whisper-url http://127.0.0.1:8178 \
  --iterations 10 \
  --output /tmp/bench_results.csv

# 3. Review results
cat /tmp/bench_results.csv
```

### 4.2 System Metrics to Capture

During each LA-* test, record:
- CPU usage (%)
- RAM used (MB) during transcription peak
- Disk I/O (temp file write/read)
- Transcription latency (ms): from POST to response
- Total end-to-end latency (ms): from button-click to DOM update

### 4.3 Machine Profiles

**Profile A — Entry laptop** (minimum viable):
- CPU: Intel Core i5, 4 cores, 2.5 GHz
- RAM: 8 GB
- Disk: SSD
- Model: `base`

**Profile B — Developer workstation** (typical):
- CPU: Apple M2 or AMD Ryzen 7, 8 cores
- RAM: 16 GB
- Disk: NVMe SSD
- Model: `small` or `turbo`

**Profile C — GPU-enabled** (power user):
- CPU: Any modern 6+ core
- GPU: NVIDIA RTX 3060+ (8 GB VRAM)
- RAM: 16 GB
- Model: `large` or `turbo` with CUDA

---

## 5. Accuracy Evaluation Notes

### 5.1 Reference Corpus

Use the following publicly available test utterances for reproducibility:

1. **LibriSpeech test-clean** (English): available at `https://www.openslr.org/12`
2. **Common Voice** (Spanish, French): available at `https://commonvoice.mozilla.org/en/datasets`
3. **Custom domain set**: prepare 10 utterances with domain-specific vocabulary (medical, legal, software)

### 5.2 WER Calculation

```python
# integration/django-prototype/dictation_demo/tests/test_wer.py
import jiwer

def compute_wer(reference: str, hypothesis: str) -> float:
    transform = jiwer.Compose([
        jiwer.ToLowerCase(),
        jiwer.RemovePunctuation(),
        jiwer.Strip(),
        jiwer.ReduceToListOfListOfWords(),
    ])
    return jiwer.wer(
        reference, hypothesis,
        truth_transform=transform,
        hypothesis_transform=transform,
    )
```

### 5.3 Known Whisper Hallucination Patterns

Be aware of these common whisper.cpp artefacts that will affect WER measurements:
- Silent recordings may produce hallucinated text (e.g., "Thank you for watching")
- Very short clips (< 1 s) may produce empty or incorrect output
- Background music causes hallucination; test in quiet environments first

### 5.4 Language Detection Validation

For `TQ-05` (mixed language): use `language=auto` parameter and verify that the detected language is reported in the response. If it chooses one language and mistranscribes the other, document this as a known limitation.

---

## 6. Test Execution Checklist

```
Before each test session:
[ ] whisper-server running and health check passes (curl http://127.0.0.1:8178/health)
[ ] whisper_bridge.py running (ws://127.0.0.1:9876)
[ ] Django dev server running (http://localhost:8000)
[ ] Browser microphone permission granted
[ ] No other audio-intensive processes running
[ ] System RAM usage < 70% baseline

After each test session:
[ ] Collect latency CSV
[ ] Record WER for each TQ-* test
[ ] Note any crashes or error messages
[ ] Document platform / model / binary version
```
