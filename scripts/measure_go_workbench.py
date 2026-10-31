#!/usr/bin/env python3
"""Measure a compiled engineering workbench over loopback; no third-party modules."""

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import math
import os
import platform
import socket
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "testdata/legacy-options"


def payload(value):
    return json.dumps(value, separators=(",", ":")).encode()


def request(port, route, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    if body is not None:
        headers = {"Origin": f"http://127.0.0.1:{port}", "X-ZenQuant-Request": "1", "Content-Type": "application/json"}
    start = time.perf_counter_ns()
    try:
        conn.request("POST" if body is not None else "GET", route, body, headers)
        response = conn.getresponse()
        data = response.read()
        elapsed = (time.perf_counter_ns() - start) / 1e6
        if response.status != 200:
            try:
                reason = json.loads(data).get("error", "unknown")
            except (ValueError, AttributeError):
                reason = "non-JSON response"
            return elapsed, f"HTTP {response.status}: {reason}", None
        try:
            value = json.loads(data) if body is not None else None
        except ValueError:
            return elapsed, "invalid JSON response", None
        return elapsed, None, value
    except (OSError, http.client.HTTPException) as exc:
        return (time.perf_counter_ns() - start) / 1e6, type(exc).__name__, None
    finally:
        conn.close()


def require(result, context):
    _, error, value = result
    if error:
        raise RuntimeError(f"{context}: {error}")
    return value


def summarize(results, elapsed_seconds):
    times = sorted(duration for duration, error, _ in results if error is None)
    errors = Counter(error for _, error, _ in results if error is not None)

    def percentile(p):
        return round(times[math.ceil(len(times) * p) - 1], 3) if times else None

    return {
        "attempts": len(results),
        "successes": len(times),
        "errors": dict(errors),
        "wall_seconds": round(elapsed_seconds, 6),
        "attempts_per_second": round(len(results) / elapsed_seconds, 2),
        "successes_per_second": round(len(times) / elapsed_seconds, 2),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
    }


def measure(port, route, body, count, workers):
    def once(_):
        return request(port, route, body)

    start = time.perf_counter_ns()
    if workers == 1:
        results = [once(i) for i in range(count)]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(once, range(count)))
    return summarize(results, (time.perf_counter_ns() - start) / 1e9)


def cpu_name():
    if platform.system() == "Darwin":
        result = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    if platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.partition(":")[2].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def rss_kib(pid):
    result = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=True)
    return int(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "bin/zenquant")
    parser.add_argument("--assets", type=Path, default=ROOT / "options-ui/dist", help="built UI assets")
    parser.add_argument("--samples", type=int, default=40, help="requests per workload (default: 40)")
    parser.add_argument("--workers", type=int, default=4, help="concurrent workers (default: 4)")
    args = parser.parse_args()
    if args.samples < 1 or args.workers < 1:
        parser.error("samples and workers must be positive")
    binary = args.binary.resolve(strict=True)
    assets = args.assets.resolve(strict=True)
    curve = json.loads((ROOT / "internal/options/testdata/call.json").read_text())["request"]
    small = payload(curve)
    curve["grid"]["count"] = 4096
    large = payload(curve)
    settings = payload(json.loads((FIXTURES / "manual-paper-settings.json").read_text()))
    intent = payload(json.loads((FIXTURES / "manual-paper-intent.json").read_text()))
    with tempfile.TemporaryDirectory(prefix="zenquant-measure-") as temp:
        # The CLI independently creates and removes its engineering journal in TMPDIR.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, TMPDIR=temp)
        with tempfile.TemporaryFile(mode="w+") as log:
            start = time.perf_counter_ns()
            process = subprocess.Popen(
                [str(binary), "serve", "--engineering", "--port", str(port), "--assets", str(assets)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(f"server exited during startup: {log.read()}")
                    _, error, _ = request(port, "/")
                    if error is None:
                        startup_ms = (time.perf_counter_ns() - start) / 1e6
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"server did not become ready: {error}")
                    time.sleep(0.01)
                idle_rss = rss_kib(process.pid)
                preview = require(request(port, "/api/paper/preview", settings), "configuration preview")
                confirmed = require(
                    request(
                        port,
                        "/api/paper/confirm",
                        payload({"preview": preview["preview"], "acknowledged_record_only": True}),
                    ),
                    "configuration confirm",
                )
                if confirmed.get("storage_scope") != "ENGINEERING_TEST_ONLY":
                    raise RuntimeError("server did not report engineering storage scope")
                workloads = {
                    "curve_3_points": ("/api/curve", small),
                    "curve_4096_points": ("/api/curve", large),
                    "paper_state": ("/api/paper/state", b"{}"),
                    "paper_preview": ("/api/paper/preview", intent),
                }
                # Validate all inputs before accepting timing results.
                for name, (route, body) in workloads.items():
                    value = require(request(port, route, body), name)
                    if name.startswith("curve_"):
                        expected = 3 if name == "curve_3_points" else 4096
                        if len(value["report"]["points"]) != expected:
                            raise RuntimeError(f"{name}: unexpected point count")
                    if name == "paper_preview" and value.get("preview") is None:
                        raise RuntimeError("paper preview was not created")
                results = {}
                for name, (route, body) in workloads.items():
                    results[name] = {
                        "serial": measure(port, route, body, args.samples, 1),
                        "concurrent": measure(port, route, body, args.samples, args.workers),
                    }
                go = subprocess.run(["go", "version"], capture_output=True, text=True)
                report = {
                    "environment": {
                        "os": platform.platform(),
                        "cpu": cpu_name(),
                        "architecture": platform.machine(),
                        "go_version": go.stdout.strip() if go.returncode == 0 else "unavailable",
                        "binary": str(binary),
                        "assets": str(assets),
                        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                        "binary_bytes": binary.stat().st_size,
                        "idle_rss_kib_ps": idle_rss,
                        "startup_to_http_ready_ms": round(startup_ms, 3),
                    },
                    "method": {
                        "samples_per_workload": args.samples,
                        "concurrent_workers": args.workers,
                        "curve_points": [3, 4096],
                        "curve_request_bytes": [len(small), len(large)],
                        "paper_request_bytes": {"state": 2, "preview": len(intent)},
                        "percentile": "nearest rank of successful requests",
                        "clock": "perf_counter_ns",
                    },
                    "results": results,
                }
                print(json.dumps(report, indent=2))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
