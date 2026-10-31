"""Synthetic end-to-end check of the Go Deribit CLI and Python reader."""

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pyarrow.parquet as pq
import pytest

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
    (parquet_path,) = tmp_path.glob("BTC/*/*.parquet")
    (json_path,) = tmp_path.glob("BTC/*/*.json")
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
