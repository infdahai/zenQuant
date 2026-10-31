"""Synthetic end-to-end check of the Go Deribit CLI and Python reader."""

import json
import os
import queue
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from zenquant.data.deribit_options_chain import OptionChainRow, save_option_chain
from zenquant.data.deribit_status import _snapshot_candidates
from zenquant.data.options_store import OptionsDataStore, OptionsDataStoreConfig


@pytest.mark.skipif(shutil.which("go") is None, reason="Go toolchain is unavailable")
def test_go_deribit_snapshot_readable_by_python(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/get_instruments?"):
                rows = [
                    {
                        "instrument_name": "BTC-X-C",
                        "expiration_timestamp": 1999999999999,
                        "strike": 100.0,
                        "option_type": "call",
                        "base_currency": "BTC",
                    }
                ]
            elif self.path.startswith("/get_book_summary_by_currency?"):
                rows = [
                    {
                        "instrument_name": "BTC-X-C",
                        "underlying_price": 100.0,
                        "open_interest": 3.0,
                        "mark_iv": 50.0,
                        "bid_price": 0,
                        "ask_price": 2.0,
                    }
                ]
            else:
                self.send_error(404)
                return
            body = json.dumps({"result": rows}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        subprocess.run(
            [
                "go",
                "run",
                "./cmd/zenquant",
                "download-deribit-options",
                "--endpoint",
                f"http://127.0.0.1:{server.server_port}",
                "--output-root",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    (parquet_path,) = tmp_path.glob("BTC/**/*.parquet")
    (json_path,) = tmp_path.glob("BTC/**/*.json")
    rows = json.loads(json_path.read_text())
    assert pq.read_table(parquet_path).to_pylist() == rows
    assert rows[0]["bid_price"] is None
    assert rows[0]["mid_price"] is None
    assert rows[0]["mark_iv_unit"] == "percent"
    assert isinstance(rows[0]["snapshot_ts_ns"], int)
    # Simulate an interrupted write: the recursive store scan must ignore it.
    orphan = parquet_path.parent / ".options-chain-orphan" / "data.parquet.tmp"
    orphan.parent.mkdir()
    shutil.copyfile(parquet_path, orphan)
    assert list((tmp_path / "BTC").glob("**/*.parquet")) == [parquet_path]
    store = OptionsDataStore(OptionsDataStoreConfig(options_dir=tmp_path))
    store.load()
    assert store.snapshot_count == 1
    assert store.lookup(rows[0]["snapshot_ts_ns"]).underlying_price == 100.0

    # The Python writer uses the same timestamp filename, but owns a separate path.
    python_json, python_parquet = save_option_chain([OptionChainRow(**rows[0])], tmp_path)
    assert python_json != json_path and python_parquet != parquet_path
    assert json.loads(json_path.read_text()) == rows
    candidates = _snapshot_candidates(tmp_path, "BTC")
    assert {path for path, _ in candidates} == {parquet_path, python_parquet}
    assert candidates[0][1] == candidates[1][1]
    store.load()
    assert store.snapshot_count == 2  # the store retains both files, even at the same timestamp


@pytest.mark.skipif(shutil.which("go") is None, reason="Go toolchain is unavailable")
def test_python_and_go_save_concurrently(tmp_path: Path) -> None:
    binary = tmp_path / "deribit.test"
    subprocess.run(["go", "test", "-c", "-o", str(binary), "./internal/deribit"], check=True, timeout=120)
    process = subprocess.Popen(
        [str(binary), "-test.run=^TestPythonConcurrentSaveHelper$", "-test.v"],
        env={**os.environ, "DERIBIT_CONCURRENT_ROOT": str(tmp_path)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    messages: queue.Queue[str] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            messages.put(line)
        messages.put("EOF")

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        while True:
            line = messages.get(timeout=30)
            assert line != "EOF", "Go helper exited before staging"
            if line.strip() == "STAGED":
                break
        row = OptionChainRow(
            snapshot_ts_ns=1700000000000000000,
            currency="BTC",
            instrument_name="PYTHON",
            expiry_ts_ms=1999999999999,
            expiry_date="2033-05-18",
            strike=100.0,
            option_type="call",
            underlying_price=100.0,
            open_interest=3.0,
            volume_usd=0.0,
            mark_iv=50.0,
            bid_price=None,
            ask_price=None,
            mid_price=None,
            underlying_index="BTC",
            settlement_period="",
        )
        python_json, python_parquet = save_option_chain([row], tmp_path)
        assert python_json == tmp_path / "BTC/2023-11/options_chain_20231114T221320Z.json"
        assert python_parquet == python_json.with_suffix(".parquet")
        assert process.stdin is not None
        process.stdin.write("publish\n")
        process.stdin.flush()
        assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=5)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()

    json_files = list((tmp_path / "BTC").rglob("*.json"))
    parquet_files = list((tmp_path / "BTC").rglob("*.parquet"))
    assert len(json_files) == len(parquet_files) == 2
    for json_path in json_files:
        parquet_path = json_path.with_suffix(".parquet")
        assert parquet_path in parquet_files
        assert json.loads(json_path.read_text()) == pq.read_table(parquet_path).to_pylist()
    assert json.loads(python_json.read_text())[0]["instrument_name"] == "PYTHON"
    go_json = next(path for path in json_files if path != python_json)
    assert json.loads(go_json.read_text())[0]["snapshot_ts_ns"] == row.snapshot_ts_ns
    assert json.loads(go_json.read_text())[0]["instrument_name"] == ""
