use options_core::{canonical::canonical_hash, error::ErrorCode, vix_observation::*};
fn fixture() -> Request {
    decode_request(include_bytes!(
        "../../options-workbench/fixtures/vix/demo.json"
    ))
    .unwrap()
}
fn result(r: &Request) -> Report {
    analyze(r).unwrap()
}
fn set_price(r: &mut Request, i: usize, n: &str) {
    r.futures.as_mut().unwrap().quotes[i].price = Price::Settlement {
        value: Some(n.parse().unwrap()),
    };
}
#[test]
fn exact_sign_spread_and_ratio_are_independent_of_spot_and_weeklies() {
    for (first, classification, spread, ratio) in [
        ("24", "BACKWARDATION", "4", "0.2"),
        ("18", "CONTANGO", "-2", "-0.1"),
        ("20", "FLAT", "0", "0"),
    ] {
        let mut r = fixture();
        set_price(&mut r, 0, first);
        let report = result(&r);
        let m = report.futures.metrics.unwrap();
        assert_eq!(m.classification, classification);
        assert_eq!(m.spread_points.canonical_string().unwrap(), spread);
        assert_eq!(
            m.ratio_minus_one
                .decimal_exact
                .unwrap()
                .canonical_string()
                .unwrap(),
            ratio
        );
        assert_eq!(
            report
                .futures
                .selected_contracts
                .iter()
                .map(|c| c.symbol.as_str())
                .collect::<Vec<_>>(),
            ["VX/V6", "VX/X6"]
        );
        assert_eq!(
            report.spot.metrics.unwrap().classification,
            "SHORT_HORIZON_HIGHER"
        );
        r.spot = None;
        assert_eq!(
            result(&r).futures.metrics.unwrap().classification,
            classification
        );
    }
    let mut r = fixture();
    set_price(&mut r, 0, "20.0000000000000000000000000001");
    assert_eq!(
        result(&r).futures.metrics.unwrap().classification,
        "BACKWARDATION"
    );
    r.futures = None;
    assert_eq!(result(&r).spot.status, Status::Ready);
}
#[test]
fn recurring_ratio_is_preserved_as_exact_fraction_without_rounding() {
    let mut r = fixture();
    set_price(&mut r, 0, "22");
    set_price(&mut r, 1, "21");
    let m = result(&r).futures.metrics.unwrap();
    assert_eq!(m.ratio_minus_one.numerator.canonical_string().unwrap(), "1");
    assert_eq!(
        m.ratio_minus_one.denominator.canonical_string().unwrap(),
        "21"
    );
    assert!(m.ratio_minus_one.decimal_exact.is_none());
    assert!(serde_json::to_vec(&result(&r)).is_ok());
}
#[test]
fn missing_front_is_not_replaced_by_farther_month_or_zero() {
    let mut r = fixture();
    r.futures.as_mut().unwrap().quotes.remove(0);
    let panel = result(&r).futures;
    assert_eq!(panel.status, Status::Missing);
    assert!(panel.metrics.is_none());
    assert_eq!(panel.selected_contracts[0].symbol, "VX/V6");
    assert_eq!(result(&r).spot.status, Status::Ready);
    r.spot.as_mut().unwrap().vix = None;
    assert_eq!(result(&r).spot.status, Status::Missing);
}
#[test]
fn freshness_uses_observed_time_and_accepts_only_the_exact_age_boundary() {
    let mut r = fixture();
    r.cutoff_ms += r.max_age_ms - 1000;
    assert_eq!(result(&r).futures.status, Status::Ready);
    r.cutoff_ms += 1;
    let p = result(&r).futures;
    assert_eq!(p.status, Status::Stale);
    assert!(p.metrics.is_none());
    r.futures.as_mut().unwrap().quotes[0].source.known_ms = r.cutoff_ms;
    assert_eq!(result(&r).futures.status, Status::Stale);
}
#[test]
fn malformed_temporal_and_source_evidence_fails_closed() {
    for case in 0..9 {
        let mut r = fixture();
        let q = &mut r.futures.as_mut().unwrap().quotes[0];
        match case {
            0 => q.source.known_ms = r.cutoff_ms + 1,
            1 => q.received_ms = q.observed_ms - 1,
            2 => q.source.known_ms = q.received_ms - 1,
            3 => q.trading_session_date = "2026-02-30".into(),
            4 => q.source.timezone = "LOCAL".into(),
            5 => q.source.sha256 = "invented".into(),
            6 => q.declared_delay_ms = Some(1001),
            7 => q.source.reference = " ".into(),
            _ => q.declared_delay_ms = None,
        }
        let p = result(&r).futures;
        assert_eq!(
            p.status,
            if case == 8 {
                Status::Missing
            } else {
                Status::Invalid
            },
            "case {case}"
        );
        assert!(p.metrics.is_none());
    }
}
#[test]
fn mixed_basis_asynchronous_and_different_sessions_are_invalid() {
    for case in 0..3 {
        let mut r = fixture();
        let q = &mut r.futures.as_mut().unwrap().quotes[1];
        match case {
            0 => q.observed_ms -= 1,
            1 => q.trading_session_date = "2026-10-15".into(),
            _ => {
                q.price = Price::Midpoint {
                    bid: Some("19".parse().unwrap()),
                    ask: Some("21".parse().unwrap()),
                }
            }
        }
        assert_eq!(result(&r).futures.status, Status::Invalid);
    }
    let mut r = fixture();
    r.spot.as_mut().unwrap().vix.as_mut().unwrap().observed_ms -= 1;
    assert_eq!(result(&r).spot.status, Status::Invalid);
}
#[test]
fn midpoint_is_exact_and_bad_books_or_denominators_never_emit_numbers() {
    let mut r = fixture();
    for (i, bid, ask) in [(0, "23", "25"), (1, "19", "21")] {
        r.futures.as_mut().unwrap().quotes[i].price = Price::Midpoint {
            bid: Some(bid.parse().unwrap()),
            ask: Some(ask.parse().unwrap()),
        };
    }
    assert_eq!(
        result(&r)
            .futures
            .metrics
            .unwrap()
            .spread_points
            .canonical_string()
            .unwrap(),
        "4"
    );
    r.futures.as_mut().unwrap().quotes[0].price = Price::Midpoint {
        bid: Some("25".parse().unwrap()),
        ask: Some("23".parse().unwrap()),
    };
    assert_eq!(result(&r).futures.reason, "CROSSED_MARKET");
    for price in ["0", "-1"] {
        let mut r = fixture();
        set_price(&mut r, 1, price);
        assert_eq!(result(&r).futures.reason, "NONPOSITIVE_PRICE");
    }
    r.futures.as_mut().unwrap().quotes[0].price = Price::Midpoint {
        bid: None,
        ask: Some("23".parse().unwrap()),
    };
    assert_eq!(result(&r).futures.status, Status::Missing);
}
#[test]
fn monthly_calendar_rejects_gaps_aliases_weekly_mislabel_and_duplicates() {
    for case in 0..8 {
        let mut r = fixture();
        let f = r.futures.as_mut().unwrap();
        match case {
            0 => {
                f.calendar.contracts.remove(2);
            }
            1 => f.calendar.contracts[0].symbol = "VX1".into(),
            2 => f.calendar.contracts[1].tenor = Tenor::Monthly,
            3 => f.calendar.contracts.push(f.calendar.contracts[0].clone()),
            4 => f.quotes.push(f.quotes[0].clone()),
            5 => f.roll_rule = "ALL_EXPIRIES".into(),
            6 => f.calendar.contracts[0].last_trade_ms = f.calendar.contracts[2].last_trade_ms,
            _ => f.calendar.contracts[0].symbol = "VX/ALIAS/V6".into(),
        }
        assert_eq!(result(&r).futures.status, Status::Invalid, "case {case}");
    }
    let mut r = fixture();
    r.futures.as_mut().unwrap().calendar.contracts.remove(0);
    assert_eq!(result(&r).futures.reason, "FIRST_MONTHLY_CONTRACT_REQUIRED");
}
#[test]
fn explicit_calendar_boundary_rolls_monthly_pair_and_preserves_roll_warning() {
    let mut r = fixture();
    let old = r.cutoff_ms;
    let f = r.futures.as_mut().unwrap();
    let expiry = f.calendar.contracts[0].last_trade_ms;
    f.previous_pair = Some(PreviousPair {
        cutoff_ms: old,
        front: "VX/V6".into(),
        next: "VX/X6".into(),
    });
    r.cutoff_ms = expiry - 1;
    let before = result(&r).futures;
    assert_eq!(before.selected_contracts[0].symbol, "VX/V6");
    assert_eq!(before.roll, Roll::UnchangedFromSuppliedPair);
    r.cutoff_ms = expiry;
    let after = result(&r).futures;
    assert_eq!(after.selected_contracts[0].symbol, "VX/X6");
    assert_eq!(after.selected_contracts[1].symbol, "VX/Z6");
    assert_eq!(after.roll, Roll::ChangedFromSuppliedPair);
    assert_eq!(after.status, Status::Stale);
    assert!(after.metrics.is_none());
    // Roll selection never refreshes prices. Explicit fresh evidence is still necessary.
    let f = r.futures.as_mut().unwrap();
    for q in &mut f.quotes {
        q.observed_ms = expiry - 1000;
        q.received_ms = expiry;
        q.source.known_ms = expiry;
        q.trading_session_date = "2026-10-21".into();
    }
    assert_eq!(result(&r).futures.status, Status::Ready);
    r.futures
        .as_mut()
        .unwrap()
        .previous_pair
        .as_mut()
        .unwrap()
        .front = "VX43/V6".into();
    assert_eq!(
        result(&r).futures.reason,
        "PREVIOUS_PAIR_DISAGREES_WITH_CALENDAR"
    );
}
#[test]
fn expired_calendar_and_exhausted_months_have_no_metrics() {
    let mut r = fixture();
    r.cutoff_ms = r.futures.as_ref().unwrap().calendar.valid_until_ms;
    assert_eq!(result(&r).futures.reason, "CALENDAR_EXPIRED");
    r.futures.as_mut().unwrap().calendar.valid_until_ms += 1;
    assert_eq!(
        result(&r).futures.reason,
        "TWO_UNEXPIRED_MONTHLY_CONTRACTS_REQUIRED"
    );
}
#[test]
fn spot_identity_basis_and_sign_remain_separate_from_futures() {
    let mut r = fixture();
    r.futures = None;
    for (n, expected) in [
        ("23", "SHORT_HORIZON_LOWER"),
        ("24", "EQUAL_HORIZON_VALUES"),
        ("25", "SHORT_HORIZON_HIGHER"),
    ] {
        r.spot.as_mut().unwrap().vix9d.as_mut().unwrap().price = Price::Index {
            value: Some(n.parse().unwrap()),
        };
        assert_eq!(result(&r).spot.metrics.unwrap().classification, expected);
    }
    r.spot.as_mut().unwrap().vix9d.as_mut().unwrap().symbol = "VX/V6".into();
    assert_eq!(result(&r).spot.reason, "WRONG_SPOT_INDEX_IDENTITY");
}
#[test]
fn strict_versioned_boundary_rejects_unknown_duplicate_and_live_claims() {
    let r = fixture();
    let mut v = serde_json::to_value(&r).unwrap();
    v["spot"]["vix"]["extra"] = true.into();
    assert!(decode_request(&serde_json::to_vec(&v).unwrap()).is_err());
    let raw = serde_json::to_string(&r).unwrap();
    assert_eq!(
        decode_request(
            raw.replacen('{', "{\"mode\":\"DEMONSTRATION\",", 1)
                .as_bytes()
        )
        .unwrap_err()
        .code,
        ErrorCode::DuplicateKey
    );
    assert!(decode_request(raw.replace("DEMONSTRATION", "LIVE").as_bytes()).is_err());
    let mut wrong = r.clone();
    wrong.schema_version = "v2".into();
    assert_eq!(analyze(&wrong).unwrap_err().code, ErrorCode::InvalidSchema);
    wrong = r.clone();
    wrong.cutoff_ms = 9_007_199_254_740_991;
    assert!(analyze(&wrong).is_err());
    assert_eq!(result(&r).input_sha256, canonical_hash(&r).unwrap());
    assert_eq!(
        serde_json::to_vec(&result(&r)).unwrap(),
        serde_json::to_vec(&result(&r)).unwrap()
    );
    assert_eq!(result(&r).evidence_status, "UNVERIFIED");
}

#[test]
fn contradictory_expiry_dates_and_unbounded_lists_are_rejected() {
    let mut r = fixture();
    r.futures.as_mut().unwrap().calendar.contracts[0].settlement_date = "2026-10-22".into();
    assert_eq!(result(&r).futures.reason, "CONFLICTING_EXPIRY_DATE");
    let mut r = fixture();
    let f = r.futures.as_mut().unwrap();
    f.quotes = vec![f.quotes[0].clone(); 33];
    assert_eq!(result(&r).futures.reason, "INPUT_LIMIT");
    let mut r = fixture();
    r.max_age_ms = 0;
    assert!(analyze(&r).is_err());
    r.max_age_ms = 86_400_001;
    assert!(analyze(&r).is_err());
}

#[test]
fn year_boundary_uses_supplied_dates_and_consecutive_months() {
    let mut r = fixture();
    let f = r.futures.as_mut().unwrap();
    f.calendar.contracts = vec![
        Contract {
            symbol: "VX/Z6".into(),
            tenor: Tenor::Monthly,
            delivery_month: 202612,
            last_trade_ms: 1_797_422_400_000,
            settlement_date: "2026-12-16".into(),
        },
        Contract {
            symbol: "VX/F7".into(),
            tenor: Tenor::Monthly,
            delivery_month: 202701,
            last_trade_ms: 1_800_446_400_000,
            settlement_date: "2027-01-20".into(),
        },
    ];
    f.calendar.first_delivery_month = 202612;
    f.quotes.truncate(2);
    f.quotes[0].symbol = "VX/Z6".into();
    f.quotes[1].symbol = "VX/F7".into();
    assert_eq!(result(&r).futures.status, Status::Ready);
}
