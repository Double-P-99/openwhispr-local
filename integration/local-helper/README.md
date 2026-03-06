# Local WebSocket Bridge — whisper_bridge.py

A lightweight local WebSocket server that bridges the browser ↔ whisper-server gap, enabling Django web apps (or any browser-based app) to perform push-to-talk dictation without requiring the full OpenWhispr Electron app.

## Architecture

```
Browser (ws://127.0.0.1:9876)
    │
    │  binary frames: raw audio (webm/ogg/wav)
    │  text frames:   {"action": "start"} / {"action": "stop"}
    ▼
whisper_bridge.py          ← this script
    │
    │  HTTP POST multipart/form-data
    ▼
whisper-server (127.0.0.1:8178)
    │
    │  JSON {"text": "transcription"}
    ▼
whisper_bridge.py → browser
```

## Requirements

- Python 3.11+
- `aiohttp` and `websockets` packages
- `whisper-server` binary running locally (from OpenWhispr or standalone)

## Installation

```bash
pip install -r requirements.txt
```

## Start the bridge

```bash
# Auto-discover whisper-server on ports 8178-8199:
python whisper_bridge.py

# Specify whisper-server URL explicitly:
python whisper_bridge.py --whisper-url http://127.0.0.1:8178

# Custom bridge port:
python whisper_bridge.py --port 9876

# Verbose logging:
python whisper_bridge.py -v
```

## Starting whisper-server (if not using OpenWhispr)

```bash
# Download a model:
wget https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin

# Start the server (binary from OpenWhispr or built from whisper.cpp):
./whisper-server --model ggml-base.bin --host 127.0.0.1 --port 8178

# Verify:
curl http://127.0.0.1:8178/health
# → {"status":"ok"}
```

## Bridge WebSocket Protocol

### Client → Bridge

**Start recording:**
```json
{"action": "start", "language": "en", "prompt": ""}
```

**Audio chunks:** binary WebSocket frames (raw audio bytes, any format FFmpeg supports)

**Stop and transcribe:**
```json
{"action": "stop"}
```

**Ping:**
```json
{"action": "ping"}
```

### Bridge → Client

**Success:**
```json
{"status": "ok", "text": "The transcribed text here."}
```

**Error:**
```json
{"status": "error", "message": "Description of the error."}
```

**Pong:**
```json
{"status": "pong"}
```

## Running latency benchmarks

```bash
# Generate a test audio file (requires ffmpeg):
ffmpeg -f lavfi -i "anullsrc=r=16000:cl=mono" -t 5 /tmp/test_5s.wav

# Or use a real speech file. Run benchmark:
python bench_latency.py \
  --audio /path/to/speech.wav \
  --whisper-url http://127.0.0.1:8178 \
  --iterations 20 \
  --output results.csv
```

## Auto-start on login (production)

### Linux (systemd user service)

```ini
# ~/.config/systemd/user/whisper-bridge.service
[Unit]
Description=OpenWhispr local WebSocket bridge
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/whisper-bridge/whisper_bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now whisper-bridge.service
```

### macOS (launchd)

```xml
<!-- ~/Library/LaunchAgents/com.openwhispr.bridge.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.openwhispr.bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/python3</string>
    <string>/opt/whisper-bridge/whisper_bridge.py</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.openwhispr.bridge.plist
```

### Windows (Task Scheduler)

```powershell
$action = New-ScheduledTaskAction -Execute "python" -Argument "C:\whisper-bridge\whisper_bridge.py"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "WhisperBridge" -Action $action -Trigger $trigger -RunLevel Highest
```

## Security notes

- The bridge binds to `127.0.0.1` only (loopback). Other machines on the network cannot connect.
- For production use, add an auth token: client sends `{"action": "start", "token": "..."}` and the bridge validates it.
- Do not expose port 9876 through a firewall or reverse proxy.
