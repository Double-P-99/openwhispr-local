#!/usr/bin/env python3
"""
bench_latency.py — Measure end-to-end transcription latency via whisper-server.

Usage:
    python bench_latency.py --audio /path/to/audio.wav --whisper-url http://127.0.0.1:8178
    python bench_latency.py --audio /path/to/audio.wav --iterations 20 --output results.csv

The script POSTs the audio file to the whisper-server /inference endpoint
repeatedly and measures latency (POST submission → JSON response).

Requirements:
    pip install requests

Output (CSV):
    iteration,audio_bytes,latency_ms,text_chars,text_preview
"""

import argparse
import csv
import io
import sys
import time

try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


def benchmark(args):
    audio_path = args.audio
    whisper_url = args.whisper_url.rstrip("/")
    iterations = args.iterations
    language = args.language

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    print(f"Audio: {audio_path} ({len(audio_bytes) // 1024} KB)")
    print(f"whisper-server: {whisper_url}")
    print(f"Language: {language or 'auto'}")
    print(f"Iterations: {iterations}")
    print()

    # Health check
    try:
        r = requests.get(f"{whisper_url}/health", timeout=3)
        r.raise_for_status()
        print(f"Health check: OK ({whisper_url}/health)")
    except Exception as exc:
        print(f"ERROR: whisper-server not reachable: {exc}", file=sys.stderr)
        sys.exit(1)

    results = []
    for i in range(1, iterations + 1):
        files = {
            "file": ("audio.wav", io.BytesIO(audio_bytes), "audio/wav"),
        }
        data = {"response_format": "json"}
        if language and language != "auto":
            data["language"] = language

        t0 = time.perf_counter()
        try:
            resp = requests.post(
                f"{whisper_url}/inference",
                files=files,
                data=data,
                timeout=args.timeout,
            )
            resp.raise_for_status()
            elapsed_ms = (time.perf_counter() - t0) * 1000
            text = resp.json().get("text", "").strip()
        except requests.exceptions.Timeout:
            print(f"  [{i:3d}] TIMEOUT")
            results.append({"iteration": i, "latency_ms": None, "error": "timeout"})
            continue
        except Exception as exc:
            print(f"  [{i:3d}] ERROR: {exc}")
            results.append({"iteration": i, "latency_ms": None, "error": str(exc)})
            continue

        row = {
            "iteration": i,
            "audio_bytes": len(audio_bytes),
            "latency_ms": round(elapsed_ms, 1),
            "text_chars": len(text),
            "text_preview": text[:60].replace("\n", " "),
        }
        results.append(row)
        print(f"  [{i:3d}] {elapsed_ms:7.1f} ms  |  {len(text):4d} chars  |  {row['text_preview']!r}")

    # Summary
    latencies = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    if latencies:
        import statistics
        print()
        print("─" * 60)
        print(f"  Iterations:  {len(latencies)}/{iterations}")
        print(f"  Min:         {min(latencies):.1f} ms")
        print(f"  Max:         {max(latencies):.1f} ms")
        print(f"  Mean:        {statistics.mean(latencies):.1f} ms")
        print(f"  Median:      {statistics.median(latencies):.1f} ms")
        if len(latencies) >= 5:
            sorted_l = sorted(latencies)
            # Standard percentile: interpolate at the 95th percentile position.
            p95_idx = min(int(0.95 * (len(sorted_l) - 1)), len(sorted_l) - 1)
            print(f"  P95:         {sorted_l[p95_idx]:.1f} ms")
        print("─" * 60)

    if args.output:
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["iteration", "audio_bytes", "latency_ms", "text_chars", "text_preview", "error"]
            )
            writer.writeheader()
            for r in results:
                writer.writerow(r)
        print(f"\nResults written to: {args.output}")


def parse_args():
    p = argparse.ArgumentParser(description="Benchmark whisper-server transcription latency.")
    p.add_argument("--audio", required=True, help="Path to audio file (WAV, WebM, OGG, etc.)")
    p.add_argument("--whisper-url", default="http://127.0.0.1:8178", dest="whisper_url",
                   help="whisper-server base URL (default: http://127.0.0.1:8178)")
    p.add_argument("--iterations", type=int, default=10, help="Number of benchmark iterations (default: 10)")
    p.add_argument("--language", default="auto", help="Language code or 'auto' (default: auto)")
    p.add_argument("--timeout", type=float, default=120.0, help="Request timeout in seconds (default: 120)")
    p.add_argument("--output", help="Optional CSV output file path")
    return p.parse_args()


if __name__ == "__main__":
    benchmark(parse_args())
