"""Read-only, content-pinned adapter for the sealed options REST panel.

Seals establish local byte integrity, never account or exchange authenticity.
Only an independently pinned manifest authorizes reads. Collector scripts and
saved derived prices are not imported or used to calculate product diagnostics.
"""

import json
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from zenquant.options.contracts import Book, BookLevel, InstrumentSpec, Observation, canonical_payload, fingerprint

_REPOSITORY = Path(__file__).resolve().parents[3]
_PANEL = _REPOSITORY / "results/crypto_opportunity/20261009_options_liquidity_panel_v1"
_SCOUT = _REPOSITORY / "results/crypto_opportunity/20261009_options_venue_scout_v1"
_SEAL_PINS = {
    _PANEL: "b61d04c898cd53545e2bc95ffdede6da6861b18d602d49cb83d57eb52816423b",
    _SCOUT: "c35edb8ad23c8d958fe8f2383786f963160842c55ea8c3489e36efb96d25b983",
}
_FAMILIES = ("Deribit_inverse", "Deribit_USDC", "Bybit", "OKX_coin")
_METADATA_FILES = {
    "Deribit_inverse": "04_deribit_instruments.raw",
    "Bybit": "05_bybit_instruments.raw",
    "OKX_coin": "06_okx_instruments.raw",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelGap:
    status: str
    reason: str
    identity: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelRecord:
    round: int
    cell_id: str
    family: str
    book: Book
    index: Observation | None
    source_fields: str
    request_hash: str
    metadata_reference: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelVerificationBinding:
    """Local loader integrity record, not authentication against Python callers.

    This digest detects changed canonical data carrying old seal references. It
    is neither an exchange-signed attestation nor an unforgeable capability.
    """

    canonical_input_hash: str
    source_identities: tuple[tuple[str, str], ...]

    def __post_init__(self):
        digest = self.canonical_input_hash
        if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("canonical_input_hash requires SHA-256 hex")
        if type(self.source_identities) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2 or any(type(value) is not str for value in pair)
            for pair in self.source_identities
        ):
            raise TypeError("source_identities requires immutable string pairs")


@dataclass(frozen=True, slots=True, kw_only=True)
class PanelInput:
    specs: tuple[InstrumentSpec, ...]
    books: tuple[Book, ...]
    observations: tuple[Observation, ...]
    identities: tuple[tuple[str, str], ...]
    assumptions: tuple[str, ...]
    records: tuple[PanelRecord, ...] = ()
    gaps: tuple[PanelGap, ...] = ()
    protocol_json: str = "{}"
    selection_json: str = "{}"
    requests: int = 0
    sealed_entries: int = 0
    verification: PanelVerificationBinding | None = None

    def __post_init__(self):
        if self.verification is not None and not isinstance(self.verification, PanelVerificationBinding):
            raise TypeError("verification requires PanelVerificationBinding or None")
        for name, kind in (
            ("specs", InstrumentSpec),
            ("books", Book),
            ("observations", Observation),
            ("records", PanelRecord),
            ("gaps", PanelGap),
            ("assumptions", str),
        ):
            values = getattr(self, name)
            if type(values) is not tuple or not all(isinstance(value, kind) for value in values):
                raise TypeError(f"{name} requires an immutable typed tuple")
        if type(self.identities) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2 or any(type(value) is not str for value in pair)
            for pair in self.identities
        ):
            raise TypeError("identities requires immutable string pairs")
        if any(
            type(getattr(self, name)) is not int or getattr(self, name) < 0 for name in ("requests", "sealed_entries")
        ):
            raise TypeError("counts require nonnegative strict integers")


def canonical_input_hash(panel: PanelInput) -> str:
    """Bind all canonical report inputs; exclude the binding to avoid recursion."""
    return fingerprint(
        {field.name: getattr(panel, field.name) for field in fields(panel) if field.name != "verification"}
    )


def input_integrity(panel: PanelInput) -> str:
    """Validate the explicit local loader record at every reporting boundary."""
    if panel.verification is None:
        return "UNVERIFIED_INPUT"
    binding = panel.verification
    if binding.canonical_input_hash != canonical_input_hash(panel) or binding.source_identities != panel.identities:
        return "VERIFICATION_BINDING_MISMATCH"
    return "PINNED_BYTES_VERIFIED"


class PanelError(ValueError):
    """Fail-closed provenance error with a machine-readable reason."""

    def __init__(self, reason: str, identity: str):
        self.reason = reason
        self.identity = identity
        super().__init__(f"{reason}: {identity}")


def _json(data: bytes | str):
    def reject(token):
        raise PanelError("NONFINITE_JSON", token)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PanelError("DUPLICATE_JSON_KEY", key)
            result[key] = value
        return result

    return json.loads(data, parse_float=Decimal, parse_constant=reject, object_pairs_hook=unique)


def _decimal(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str | Decimal):
        raise PanelError("DECIMAL_REQUIRED", str(type(value)))
    result = Decimal(value)
    if not result.is_finite():
        raise PanelError("NONFINITE_INPUT", str(value))
    return result


def _integer(value, *, string=False):
    if string and type(value) is str and value.isdecimal():
        return int(value)
    if type(value) is not int or value < 0:
        raise PanelError("INTEGER_REQUIRED", str(value))
    return value


def _ns(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise PanelError("UTC_REQUIRED", value)
    delta = dt.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000


def _path(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts or relative == "seal.json":
        raise PanelError("UNSAFE_PATH", relative)
    target = root / path
    if not target.resolve().is_relative_to(root) or any(
        (root / Path(*path.parts[:i])).is_symlink() for i in range(1, len(path.parts) + 1)
    ):
        raise PanelError("UNSAFE_PATH", relative)
    return target


def _sealed(root):
    root = root.resolve()
    if root not in _SEAL_PINS:
        raise PanelError("UNPINNED_SEAL", str(root))
    seal_path = root / "seal.json"
    if seal_path.is_symlink():
        raise PanelError("UNSAFE_PATH", "seal.json")
    seal_bytes = seal_path.read_bytes()
    if sha256(seal_bytes).hexdigest() != _SEAL_PINS[root]:
        raise PanelError("SEAL_HASH_MISMATCH", str(root))
    files = _json(seal_bytes)["files"]
    verified = {}
    for name, info in files.items():
        data = _path(root, name).read_bytes()
        if sha256(data).hexdigest() != info["sha256"] or len(data) != _integer(info["bytes"]):
            raise PanelError("HASH_MISMATCH", name)
        verified[name] = data
    return verified


def _read(files, name):
    if name not in files:
        raise PanelError("UNSEALED_INPUT", name)
    return _json(files[name])


def _payload(value):
    if isinstance(value, list | tuple):
        return [_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _payload(item) for key, item in value.items()}
    return canonical_payload(value)


def _equivalent(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equivalent(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equivalent(x, y) for x, y in zip(a, b))
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    try:
        return _decimal(a) == _decimal(b)
    except (ValueError, InvalidOperation):
        return type(a) is type(b) and a == b


def _render(value):
    return json.dumps(_payload(value), sort_keys=True, separators=(",", ":"))


def _metadata_rows(raw, family):
    if family.startswith("Deribit"):
        result = raw["result"]
        return result if isinstance(result, list) else [result]
    return raw["result"]["list"] if family == "Bybit" else raw["data"]


def _symbol(metadata, family):
    return (
        metadata["instrument_name"]
        if family.startswith("Deribit")
        else metadata["symbol"]
        if family == "Bybit"
        else metadata["instId"]
    )


def _spec(metadata, family, cell, digest, known_ns):
    if family.startswith("Deribit"):
        expiry = _integer(metadata["expiration_timestamp"])
        strike = _decimal(metadata["strike"])
        side = "C" if metadata["option_type"] == "call" else "P"
        base, quote, settle, strike_currency = (
            metadata[k] for k in ("base_currency", "quote_currency", "settlement_currency", "counter_currency")
        )
        inverse = family == "Deribit_inverse"
        if metadata["instrument_type"] != ("reversed" if inverse else "linear"):
            raise PanelError("PRODUCT_MISMATCH", family)
        if _decimal(metadata["contract_size"]) != 1:
            raise PanelError("MULTIPLIER_MISMATCH", family)
        minimum = step = _decimal(metadata["min_trade_amount"])
        multiplier, maximum = Decimal(1), None
        bands = ((Decimal(0), _decimal(metadata["tick_size"])),) + tuple(
            (_decimal(b["above_price"]), _decimal(b["tick_size"])) for b in metadata.get("tick_size_steps", [])
        )
    elif family == "Bybit":
        expiry = _integer(metadata["deliveryTime"], string=True)
        strike = _decimal(metadata["symbol"].split("-")[2])
        side = "C" if metadata["optionsType"] == "Call" else "P"
        base, quote, settle = (metadata[k] for k in ("baseCoin", "quoteCoin", "settleCoin"))
        strike_currency = quote
        lot = metadata["lotSizeFilter"]
        minimum, step, maximum = (_decimal(lot[k]) for k in ("minOrderQty", "qtyStep", "maxOrderQty"))
        multiplier = Decimal(1)
        bands = ((Decimal(0), _decimal(metadata["priceFilter"]["tickSize"])),)
    else:
        if metadata["instType"] != "OPTION" or metadata["ctType"] != "inverse" or metadata["uly"] != "BTC-USD":
            raise PanelError("PRODUCT_MISMATCH", family)
        expiry, strike, side = (
            _integer(metadata["expTime"], string=True),
            _decimal(metadata["stk"]),
            metadata["optType"],
        )
        base, quote, settle, strike_currency = metadata["ctValCcy"], "BTC", metadata["settleCcy"], "USD"
        multiplier = _decimal(metadata["ctVal"]) * _decimal(metadata["ctMult"])
        minimum, step = _decimal(metadata["minSz"]), _decimal(metadata["lotSz"])
        maximum = _decimal(metadata["maxLmtSz"]) if metadata.get("maxLmtSz") else None
        bands = ((Decimal(0), _decimal(metadata["tickSz"])),)
    if (
        expiry != _integer(cell["expiry_ms"])
        or strike != _decimal(cell["strike"])
        or side != cell["side"]
        or base != "BTC"
    ):
        raise PanelError("CONTRACT_JOIN_MISMATCH", family)
    inverse = family in ("Deribit_inverse", "OKX_coin")
    if (
        (family == "Deribit_inverse" and (quote, settle) != ("BTC", "BTC"))
        or (family == "Deribit_USDC" and (quote, settle, strike_currency) != ("USDC", "USDC", "USDC"))
        or (family == "Bybit" and (quote, settle) != ("USDT", "USDT"))
    ):
        raise PanelError("DENOMINATION_MISMATCH", family)
    return InstrumentSpec(
        venue="Deribit" if family.startswith("Deribit") else "Bybit" if family == "Bybit" else "OKX",
        instrument_id=_symbol(metadata, family),
        product="inverse_option" if inverse else "linear_option",
        underlying=base,
        quote_currency=quote,
        strike_currency=strike_currency,
        settlement_currency=settle,
        margin_currency=settle,
        expiry_ns=expiry * 10**6,
        strike=strike,
        option_type=side,
        exercise_style="UNKNOWN",
        quantity_unit="contracts" if family == "OKX_coin" else "base",
        multiplier=multiplier,
        price_unit="quote_per_base",
        min_quantity_native=minimum,
        quantity_step_native=step,
        max_quantity_native=maximum,
        tick_bands=bands,
        settlement_index="UNKNOWN",
        settlement_window_ns=1,
        settlement_rule="inverse_via_future" if inverse else "linear_cash",
        rule_version="UNVERIFIED_SNAPSHOT_PROXY",
        metadata_hash=digest,
        valid_from_ns=known_ns,
        valid_until_ns=None,
        provenance="proxy",
    )


def _book(raw, record, spec):
    family = record["family"]
    query = parse_qs(urlparse(record["url"]).query)
    url = urlparse(record["url"])
    expected_host = (
        "www.deribit.com" if spec.venue == "Deribit" else "api.bybit.com" if spec.venue == "Bybit" else "www.okx.com"
    )
    key = "instrument_name" if spec.venue == "Deribit" else "symbol" if spec.venue == "Bybit" else "instId"
    expected_path = (
        "/api/v2/public/get_order_book"
        if spec.venue == "Deribit"
        else "/v5/market/orderbook"
        if spec.venue == "Bybit"
        else "/api/v5/market/books"
    )
    if (
        url.path != expected_path
        or url.scheme != "https"
        or url.hostname != expected_host
        or query.get(key) != [spec.instrument_id]
        or record["venue"] != spec.venue
    ):
        raise PanelError("REQUEST_IDENTITY_MISMATCH", record["name"])
    if record.get("http_status") != 200 or record.get("error") or record.get("api_error"):
        raise PanelError("API_STATUS", record["name"])
    if spec.venue == "Deribit":
        if raw.get("testnet") is not False or raw.get("error") or raw.get("result", {}).get("state") != "open":
            raise PanelError("API_STATUS", record["name"])
        body = raw["result"]
        if body["instrument_name"] != spec.instrument_id:
            raise PanelError("SYMBOL_MISMATCH", record["name"])
        bids, asks, source = body["bids"], body["asks"], _integer(body["timestamp"])
    elif spec.venue == "Bybit":
        if type(raw.get("retCode")) is not int or raw["retCode"] != 0:
            raise PanelError("API_STATUS", record["name"])
        body = raw["result"]
        if body["s"] != spec.instrument_id:
            raise PanelError("SYMBOL_MISMATCH", record["name"])
        bids, asks, source = body["b"], body["a"], _integer(body["ts"])
    else:
        if raw.get("code") != "0" or len(raw.get("data", [])) != 1:
            raise PanelError("API_STATUS", record["name"])
        body = raw["data"][0]
        bids, asks, source = body["bids"], body["asks"], _integer(body["ts"], string=True)
    received = _ns(record["completed_at"])
    if spec.valid_from_ns > received:
        raise PanelError("METADATA_NOT_YET_KNOWN", record["name"])
    book = Book(
        spec=spec,
        bids=tuple(BookLevel(price=_decimal(p), quantity_native=_decimal(q)) for p, q, *_ in bids),
        asks=tuple(BookLevel(price=_decimal(p), quantity_native=_decimal(q)) for p, q, *_ in asks),
        source_ns=source * 10**6,
        received_ns=received,
        available_ns=received,
        raw_hash=record["sha256"],
    )
    index = None
    if family.startswith("Deribit") and body.get("index_price") is not None:
        index = Observation(
            value=_decimal(body["index_price"]),
            source_ns=book.source_ns,
            received_ns=received,
            available_ns=received,
            source_id=f"{family}:{spec.instrument_id}:{record['sha256']}:index_price",
            provenance="observed",
        )
    return book, index, _render(body)


def _load_panel(root: Path) -> PanelInput:
    """Canonicalize a pinned local snapshot; untrusted JSON cannot select roots."""
    files = _sealed(root)
    selection = _read(files, "selection.json")
    protocol = _read(files, "preregistration.json")
    ledger = _read(files, "request_ledger.json")
    rounds = _read(files, "rounds.json")
    ph = sha256(files["preregistration.json"]).hexdigest()
    if ledger["preregistration_sha256"] != ph:
        raise PanelError("PROTOCOL_HASH_MISMATCH", "preregistration")
    requests = {}
    raw_paths = set()
    for request in ledger["requests"]:
        name, raw_path = request["name"], request["raw_path"]
        if name in requests or raw_path in raw_paths:
            raise PanelError("DUPLICATE_REQUEST", name)
        if raw_path not in files:
            raise PanelError("UNSEALED_INPUT", raw_path)
        if len(files[raw_path]) != request["bytes"] or sha256(files[raw_path]).hexdigest() != request["sha256"]:
            raise PanelError("HASH_MISMATCH", raw_path)
        requests[name] = request
        raw_paths.add(raw_path)
    if "collection_status.json" in files:
        status = _read(files, "collection_status.json")
        if status["selection_sha256"] != sha256(files["selection.json"]).hexdigest():
            raise PanelError("SELECTION_HASH_MISMATCH", "selection")
    source_files = files
    source_identity = "fixture-local"
    if "base_metadata_source" in selection:
        # Source string is compared, never used as a filesystem authority.
        if selection["base_metadata_source"] != str(_SCOUT):
            raise PanelError("UNAPPROVED_METADATA_ROOT", selection["base_metadata_source"])
        source_files = _sealed(_SCOUT)
        source_identity = _SEAL_PINS[_SCOUT]
        for name, digest in selection["base_metadata_hashes"].items():
            if sha256(source_files[f"raw/{name}"]).hexdigest() != digest:
                raise PanelError("METADATA_HASH_MISMATCH", name)
        budget_requests = _read(source_files, "budget.json")["requests"]
        for name, captured in selection["base_metadata_captured_at"].items():
            receipts = [r["completed_at"] for r in budget_requests if r["name"] + ".raw" == name]
            if receipts != [captured]:
                raise PanelError("METADATA_TIME_MISMATCH", name)
    cells, specs, gaps, metadata_refs = {}, {}, [], {}
    for cell in selection["cells"]:
        if cell["id"] in cells:
            raise PanelError("DUPLICATE_CELL", cell["id"])
        cells[cell["id"]] = cell
        for family in _FAMILIES:
            identity = (cell["id"], family)
            if family not in cell["symbols"] or family not in cell["metadata"]:
                gaps.append(PanelGap(status="NOT_EVALUABLE", reason="MISSING_FAMILY", identity=":".join(identity)))
                continue
            known = 0
            if "local_metadata_sources" in selection:
                reference = selection["local_metadata_sources"][family]
                raw = _read(files, reference)
                data = files[reference]
            elif family == "Deribit_USDC":
                matching = [
                    r
                    for r in requests.values()
                    if r["name"].startswith("setup_linear_")
                    and parse_qs(urlparse(r["url"]).query).get("instrument_name") == [cell["symbols"][family]]
                ]
                if len(matching) != 1:
                    raise PanelError("METADATA_REQUEST_IDENTITY", ":".join(identity))
                request = matching[0]
                reference, known = request["raw_path"], _ns(request["completed_at"])
                raw, data = _read(files, reference), files[reference]
            else:
                name = _METADATA_FILES[family]
                reference, known = f"raw/{name}", _ns(selection["base_metadata_captured_at"][name])
                raw, data = _read(source_files, reference), source_files[reference]
            matching = [m for m in _metadata_rows(raw, family) if _symbol(m, family) == cell["symbols"][family]]
            if len(matching) != 1 or not _equivalent(matching[0], cell["metadata"][family]):
                raise PanelError("METADATA_IDENTITY_MISMATCH", ":".join(identity))
            specs[identity] = _spec(matching[0], family, cell, sha256(data).hexdigest(), known)
            metadata_refs[identity] = (
                f"{source_identity if family != 'Deribit_USDC' else _SEAL_PINS[root.resolve()]}:{reference}"
            )
    selection_observations = []
    if "fresh_bybit_ticker_timestamp_ms" in selection:
        matching_requests = [r for r in requests.values() if r["name"] == "setup_bybit_tickers"]
        if len(matching_requests) != 1:
            raise PanelError("TICKER_REQUEST_MISSING", "selection")
        request = matching_requests[0]
        ticker = _read(files, request["raw_path"])
        timestamp = _integer(ticker["time"])
        if (
            request["http_status"] != 200
            or ticker["retCode"] != 0
            or timestamp != selection["fresh_bybit_ticker_timestamp_ms"]
        ):
            raise PanelError("TICKER_IDENTITY_MISMATCH", "selection")
        received = _ns(request["completed_at"])
        if received > _ns(selection["selected_at"]):
            raise PanelError("TICKER_NOT_YET_KNOWN", "selection")
        for cell in cells.values():
            rows = [row for row in ticker["result"]["list"] if row["symbol"] == cell["symbols"].get("Bybit")]
            if len(rows) != 1:
                raise PanelError("TICKER_SYMBOL_MISMATCH", cell["id"])
            for source_field, selection_field in (
                ("underlyingPrice", "reference_forward"),
                ("delta", "Bybit_delta_at_selection"),
            ):
                if not _equivalent(rows[0][source_field], cell[selection_field]):
                    raise PanelError("TICKER_VALUE_MISMATCH", cell["id"])
                selection_observations.append(
                    Observation(
                        value=_decimal(rows[0][source_field]),
                        source_ns=timestamp * 10**6,
                        received_ns=received,
                        available_ns=received,
                        source_id=f"Bybit:{cell['symbols']['Bybit']}:{request['sha256']}:{source_field}",
                        provenance="observed",
                    )
                )
    records, seen = [], set()
    for round_row in rounds:
        number = _integer(round_row["round"])
        for record in round_row["records"]:
            identity = (number, record["cell_id"], record["family"])
            if identity in seen or record["round"] != number:
                raise PanelError("DUPLICATE_BOOK_IDENTITY", str(identity))
            seen.add(identity)
            request = requests.get(record["name"])
            if request is None or any(record.get(k) != v for k, v in request.items()):
                raise PanelError("LEDGER_ROUND_MISMATCH", record["name"])
            spec = specs.get(identity[1:])
            if spec is None:
                raise PanelError("UNSELECTED_INSTRUMENT", str(identity))
            try:
                book, index, source_fields = _book(_read(files, record["raw_path"]), record, spec)
            except PanelError as exc:
                gaps.append(PanelGap(status="INVALID", reason=exc.reason, identity=exc.identity))
                continue
            records.append(
                PanelRecord(
                    round=number,
                    cell_id=record["cell_id"],
                    family=record["family"],
                    book=book,
                    index=index,
                    source_fields=source_fields,
                    request_hash=record["sha256"],
                    metadata_reference=metadata_refs[identity[1:]],
                )
            )
    for number in {_integer(r["round"]) for r in rounds}:
        for cell_id, family in specs:
            if (number, cell_id, family) not in seen:
                gaps.append(
                    PanelGap(status="NOT_EVALUABLE", reason="MISSING_BOOK", identity=f"{number}:{cell_id}:{family}")
                )
    gaps.extend(
        PanelGap(status="NOT_EVALUABLE", reason=reason, identity="panel")
        for reason in (
            "SEQUENCE_CONTINUITY_UNPROVEN",
            "ACCOUNT_MODE_UNVERIFIED",
            "SETTLEMENT_RULE_UNVERIFIED",
            "ETH_METADATA_NOT_COLLECTED",
            "ACCOUNT_FEE_TIER_UNVERIFIED",
        )
    )
    assumptions = (
        "available_ns=transport completion knowledge bound; durable availability independently unlogged",
        "local/exchange clock synchronization unverified; -1s tolerance is comparison policy",
        "Deribit quantity step=min_trade_amount proxy; not verified account precision",
        "Bybit quantity interpreted as base; source has no explicit contract_size",
        "OKX premium BTC/base and strike USD are product proxies; empty baseCcy/quoteCcy prove neither",
        "metadata valid_from is observed knowledge bound, not rule effective date; fixture bound0",
        "UNKNOWN exercise/final settlement index; hypothetical rule and window1ns sentinel do not establish settlement",
        "Deribit tick thresholds preserved; boundary inclusivity/history unknown",
        "USD/USDT/USDC parity proxy, FX conversion reachability unverified",
        "rate/caps preregistered proxies; account tiers/delivery fees unverified",
        "REST sequence domains retained raw; snapshots do not prove continuity or fills",
        "inverse families UNSUPPORTED for P1 execution; linear account/rules NOT_EVALUABLE",
    )
    identities = (
        ("panel_seal", _SEAL_PINS[root.resolve()]),
        ("metadata_seal", source_identity),
        ("selection", sha256(files["selection.json"]).hexdigest()),
        ("preregistration", ph),
        ("request_ledger", sha256(files["request_ledger.json"]).hexdigest()),
        ("rounds", sha256(files["rounds.json"]).hexdigest()),
    )
    return PanelInput(
        specs=tuple(specs.values()),
        books=tuple(r.book for r in records),
        observations=tuple(selection_observations) + tuple(r.index for r in records if r.index is not None),
        identities=identities,
        assumptions=assumptions,
        records=tuple(records),
        gaps=tuple(gaps),
        protocol_json=_render(protocol),
        selection_json=_render(selection),
        requests=len(requests),
        sealed_entries=len(files),
    )


def load_panel(root: Path) -> PanelInput:
    """Use the fixed Decimal context even when the caller changes its context."""
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        panel = _load_panel(root)
        binding = PanelVerificationBinding(
            canonical_input_hash=canonical_input_hash(panel), source_identities=panel.identities
        )
        return replace(panel, verification=binding)
