"""Off-chain refresh lifecycle for the live OptionsState sidecar store."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from zenquant.data import OptionsDataStore, OptionsDataStoreConfig

OptionsStoreFactory = Callable[[OptionsDataStoreConfig], OptionsDataStore]


class OptionsShadowStoreRefresher:
    """Reload local Parquet into a new store and atomically publish last-good.

    File discovery and Parquet loading run only in the worker (or the explicit
    synchronous preload before event processing). Decision handlers read the
    current immutable store reference and may only signal this worker.
    """

    def __init__(
        self,
        *,
        config: OptionsDataStoreConfig,
        refresh_interval_s: float,
        store_factory: OptionsStoreFactory = OptionsDataStore,
    ) -> None:
        if refresh_interval_s <= 0.0:
            raise ValueError("options shadow refresh interval must be positive")
        self._config = config
        self._refresh_interval_s = refresh_interval_s
        self._store_factory = store_factory
        self._current_store = store_factory(config)
        self._file_identity: tuple[tuple[str, int, int], ...] = ()
        self._refresh_requested = threading.Event()
        self._stop_requested = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_error = ""
        self._swap_count = 0

    @property
    def current_store(self) -> OptionsDataStore:
        """Return the last-good store using a single atomic reference read."""
        return self._current_store

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def swap_count(self) -> int:
        return self._swap_count

    def preload(self) -> OptionsDataStore:
        """Synchronously load initial files before event processing starts."""
        if self.is_running:
            raise RuntimeError("options shadow preload must run before worker start")
        self._refresh_if_changed()
        return self._current_store

    def start(self) -> None:
        """Start one daemon refresh worker; repeated starts are harmless."""
        with self._lifecycle_lock:
            if self.is_running:
                return
            self._stop_requested.clear()
            thread = threading.Thread(
                target=self._run,
                name="zenquant-options-shadow-refresh",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def request_refresh(self) -> None:
        """Wake the worker without doing filesystem or loader work inline."""
        self._refresh_requested.set()

    def stop(self) -> None:
        """Stop and join the worker so application shutdown leaks no thread."""
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_requested.set()
            self._refresh_requested.set()
        thread.join()
        with self._lifecycle_lock:
            if self._thread is thread:
                self._thread = None

    def _run(self) -> None:
        while not self._stop_requested.is_set():
            self._refresh_requested.wait(self._refresh_interval_s)
            self._refresh_requested.clear()
            if self._stop_requested.is_set():
                break
            self._refresh_if_changed()

    def _refresh_if_changed(self) -> None:
        try:
            identity = self._options_files()
            if identity == self._file_identity:
                return
            candidate = self._store_factory(self._config)
            candidate.load()
            if self._stop_requested.is_set():
                return
            # One reference assignment is the publication boundary. Existing
            # handlers holding the previous object finish safely on last-good.
            self._current_store = candidate
            self._file_identity = identity
            self._swap_count += 1
            self._last_error = ""
        except Exception as exc:
            # Keep both the last-good reference and old identity so the next
            # signal or periodic pass retries the failed local publication.
            self._last_error = f"{type(exc).__name__}: {exc}"

    def _options_files(self) -> tuple[tuple[str, int, int], ...]:
        currency_dir = self._config.options_dir / self._config.currency
        files: list[tuple[str, int, int]] = []
        for path in sorted(currency_dir.glob("**/*.parquet")) if currency_dir.exists() else ():
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((_stable_path(path), stat.st_mtime_ns, stat.st_size))
        return tuple(files)


def _stable_path(path: Path) -> str:
    return str(path)
