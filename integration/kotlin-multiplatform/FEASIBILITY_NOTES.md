# Kotlin Multiplatform — Feasibility Notes

**Verdict: Not recommended for this use case.**

This document explains why Kotlin Multiplatform (KMP) would not materially improve the OpenWhispr-Django integration, and under what narrow circumstances it might make sense.

---

## What KMP Could Theoretically Do

Kotlin Multiplatform allows sharing business logic (not UI) across JVM desktop, Android, iOS, and web targets. A KMP wrapper for OpenWhispr would:

1. Call the `whisper-server` HTTP API from shared Kotlin code  
2. Expose a `DictationService` interface implemented per-platform  
3. Integrate into a Compose Multiplatform UI or a Kotlin/JS web frontend

### Hypothetical KMP architecture

```
┌──────────────────────────────────────────────────────────────┐
│  KMP Shared Module (commonMain)                               │
│    DictationService                                           │
│      fun startRecording()                                     │
│      fun stopAndTranscribe(): Flow<String>                    │
│    WhisperHttpClient (Ktor)                                   │
│      POST /inference → text                                   │
└───────────────┬──────────────┬──────────────────────────────┘
                │              │
    ┌───────────▼──┐    ┌──────▼────────────┐
    │ Desktop (JVM) │    │  Android           │
    │ Compose MP   │    │  (microphone API)  │
    └───────────────┘    └────────────────────┘
```

---

## Why KMP Does Not Help Here

### 1. The bottleneck is not the programming language

The hard parts of this integration are:

| Problem | KMP solves it? |
|---|---|
| Browser cannot call localhost whisper-server directly (CORS) | ❌ (not a browser app) |
| Desktop companion must be running alongside Django | ❌ (same constraint) |
| whisper-server is a compiled binary (not a library) | ❌ |
| Audio capture via MediaRecorder in browser | ❌ |
| Text insertion into a specific DOM field | ❌ |

KMP excels at sharing *business logic* (parsers, data models, sync algorithms). The integration here is fundamentally a system architecture problem, not a logic-sharing problem.

### 2. KMP cannot simplify the companion dependency

Whether the companion is a Python script, a Node.js process, or a Kotlin/JVM app, it still needs to:
- Run as a daemon on the user's machine  
- Manage the `whisper-server` binary lifecycle  
- Expose a local WebSocket or HTTP endpoint  

A 250-line Python script (`whisper_bridge.py`) does exactly this. The equivalent Kotlin implementation would be 400–600 lines (plus Gradle setup, Ktor configuration, Kotlin Native or JVM packaging). The Python version has zero build complexity and ships as a single file.

### 3. KMP mobile (Android/iOS) is a different story

If the *product* itself is a mobile app that needs push-to-talk dictation (not a Django web form):
- Android: use Android's built-in `SpeechRecognizer` or `AudioRecord` + whisper.cpp JNI
- iOS: use `AVAudioEngine` + whisper.cpp via Swift Package
- KMP shared `DictationService` could abstract both

But this is only relevant if you are building a *mobile-first* product, not augmenting a Django web app.

### 4. Compose Multiplatform is not a substitute for React + Django

Your current stack is Django (backend) + React/HTML (frontend). Rewriting this in Compose Multiplatform would be a complete product rewrite for no user-facing benefit.

---

## When KMP Would Make Sense

Recommend KMP **only** if all of the following are true:

1. You are building a **native desktop app** (not a web form in a browser)  
2. The desktop app needs to run on **macOS, Windows, and Linux** with a single codebase  
3. You need **mobile (Android/iOS) support** in the same codebase  
4. The team has **Kotlin expertise** and is comfortable with the KMP toolchain

In that scenario, a KMP desktop+mobile app with `whisper.cpp` (via Kotlin/Native FFI or JNI on JVM) is technically feasible and would be more maintainable than maintaining three separate native codebases.

---

## Prototype Stub (Reference Only)

The following Kotlin code shows what a minimal `WhisperClient` would look like using Ktor. It is provided for reference only — **do not build this** for the Django integration use case.

```kotlin
// commonMain/kotlin/com/example/dictation/WhisperClient.kt
package com.example.dictation

import io.ktor.client.*
import io.ktor.client.request.*
import io.ktor.client.request.forms.*
import io.ktor.client.statement.*
import io.ktor.http.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
data class TranscriptionResult(val text: String)

class WhisperClient(
    private val baseUrl: String = "http://127.0.0.1:8178",
    private val httpClient: HttpClient = HttpClient(),
) {
    /**
     * POST raw audio bytes to the local whisper-server /inference endpoint.
     * Returns the transcribed text.
     *
     * @param audioBytes Raw audio in any format FFmpeg supports (WebM, WAV, OGG).
     * @param language   ISO 639-1 language code, or null for auto-detect.
     */
    suspend fun transcribe(audioBytes: ByteArray, language: String? = null): String {
        val response: HttpResponse = httpClient.submitFormWithBinaryData(
            url = "$baseUrl/inference",
            formData = formData {
                append("response_format", "json")
                language?.let { append("language", it) }
                append(
                    key = "file",
                    value = audioBytes,
                    headers = Headers.build {
                        append(HttpHeaders.ContentDisposition, "filename=audio.webm")
                        append(HttpHeaders.ContentType, "audio/webm")
                    }
                )
            }
        )
        val body = response.bodyAsText()
        return Json.decodeFromString<TranscriptionResult>(body).text.trim()
    }
}
```

```kotlin
// Expected Gradle dependencies (build.gradle.kts, commonMain):
// implementation("io.ktor:ktor-client-core:2.3.x")
// implementation("io.ktor:ktor-client-content-negotiation:2.3.x")
// implementation("io.ktor:ktor-serialization-kotlinx-json:2.3.x")
// // platform-specific engines:
// // jvmMain: io.ktor:ktor-client-okhttp
// // nativeMain: io.ktor:ktor-client-darwin (iOS) or ktor-client-curl (Linux/macOS)
```

**Blockers for a real KMP implementation:**
- `whisper-server` binary must still be shipped separately (not a KMP concern)  
- Audio capture via microphone is platform-specific API in each KMP target  
- `kotlin.native` targets require Kotlin/Native compiler toolchain and cross-compilation  
- GGML model files are large (75 MB–3 GB) and must be bundled or downloaded separately

---

## Final Verdict

| Path | Effort | Value added vs Python bridge | Recommend? |
|---|---|---|---|
| Python `whisper_bridge.py` | 1 day | Baseline | ✅ Yes |
| Node.js bridge | 1 day | Marginally better TypeScript types | Maybe |
| KMP companion (JVM) | 1–2 weeks | Kotlin type safety, no new value | ❌ No |
| KMP mobile app | 4–8 weeks | Full cross-platform native app | Only if mobile-first |
| Native Electron (existing) | Already done | Same as current OpenWhispr | Use as-is |

**Conclusion:** Use the Python bridge. It is simple, ships in a single file, requires no build system, and is trivially auditable. KMP adds complexity without solving any of the actual integration problems.
