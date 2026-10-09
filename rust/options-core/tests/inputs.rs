use options_core::{codec, contracts::*, decimal::Dec, fixtures::mathematical_request, inputs};
fn request() -> Request {
    mathematical_request(
        "terminal-eth-call",
        Dec::parse("1").unwrap(),
        Dec::parse("1").unwrap(),
        vec![],
        RiskPolicy {
            per_intent_report: Dec::one(),
            active_and_pending_report: Dec::one(),
            rolling_tail_paid_report: Dec::one(),
            rolling_window_ns: 1,
            report_currency: "USDC".into(),
            fx_timing: None,
        },
    )
    .unwrap()
}
#[test]
fn recursive_codec() {
    assert!(codec::decode::<serde_json::Value>(br#"{"a":{"x":1,"x":2}}"#, 100).is_err());
    assert!(codec::decode::<serde_json::Value>(b"[0.1]", 100).is_err());
}
#[test]
fn economic_admission() {
    let mut r = request();
    r.quotes[0].book.spec.multiplier = Dec::zero();
    assert!(inputs::load_analysis(r).is_err());
}
#[test]
fn valid_fixture_history() {
    let r = request();
    let a = inputs::load_analysis(r.clone()).unwrap();
    assert_eq!(a.view_history, r.views);
}
#[test]
fn revisions_and_future_retention() {
    let mut r = request();
    let mut future = r.views[0].clone();
    future.meta.revision = 2;
    future.meta.parent_hash = Some(options_core::canonical::canonical_hash(&r.views[0]).unwrap());
    future.meta.received_ns = 120;
    future.meta.available_ns = 120;
    future.meta.cutoff_ns = 120;
    r.views.push(future.clone());
    r.cutoff_ns = 100;
    let a = inputs::load_analysis(r.clone()).unwrap();
    assert_eq!(a.view.meta.revision, 1);
    assert_eq!(a.view_history.len(), 2);
    assert_eq!(
        inputs::select_view(&r.views, &r.view_id, 120).unwrap(),
        future
    );
    r.views[1].meta.parent_hash = Some("0".repeat(64));
    assert!(inputs::load_analysis(r).is_err());
}
#[test]
fn late_evidence_kept() {
    let mut r = request();
    r.quotes[0].fee_known_ns = Some(120);
    r.quotes[0].metadata_known_ns = Some(120);
    r.quotes[0].quote_to_report.as_mut().unwrap().received_ns = 120;
    r.quotes[0].quote_to_report.as_mut().unwrap().available_ns = 120;
    assert!(inputs::load_analysis(r).is_ok());
    let mut r = request();
    r.quotes[0].book.received_ns = 120;
    r.quotes[0].book.available_ns = 120;
    let hash = options_core::canonical::canonical_hash(&r.quotes[0].book.spec).unwrap();
    let a = inputs::load_analysis(r).unwrap();
    assert_eq!(a.quotes.len(), 1);
    assert!(
        inputs::select_quote(&a.quotes, &hash, 100)
            .unwrap()
            .is_none()
    );
}
#[test]
fn schemas_numeric_and_limits() {
    let r = request();
    let mut v = serde_json::to_value(&r).unwrap();
    v["cutoff_ns"] = true.into();
    assert!(inputs::decode_request(&serde_json::to_vec(&v).unwrap()).is_err());
    v = serde_json::to_value(&r).unwrap();
    v["authority"] = true.into();
    assert!(inputs::decode_request(&serde_json::to_vec(&v).unwrap()).is_err());
    let mut r = r;
    r.schema_version = "future".into();
    assert!(inputs::load_analysis(r).is_err());
    assert!(inputs::decode_request(&vec![b' '; MAX_REQUEST_BYTES + 1]).is_err());
    let depth = format!("{}0{}", "[".repeat(65), "]".repeat(65));
    assert!(codec::decode::<serde_json::Value>(depth.as_bytes(), 1000).is_err());
    assert!(codec::decode::<serde_json::Value>(b"{} {}", 100).is_err());
}
#[test]
fn conflicting_quotes_and_venue_identity() {
    let mut r = request();
    let mut q = r.quotes[0].clone();
    q.quote_id = "different".into();
    q.book.asks[0].price = Dec::parse("3").unwrap();
    r.quotes.push(q);
    assert!(inputs::load_analysis(r).is_err());
    let mut r = request();
    let mut q = r.quotes[0].clone();
    q.book.spec.venue = "OTHER".into();
    r.quotes.push(q);
    assert!(inputs::load_analysis(r).is_ok());
}
#[test]
fn nested_money_probabilities_wallets() {
    for mutate in 0..6 {
        let mut r = request();
        match mutate {
            0 => r.quotes[0].fee_policy.as_mut().unwrap().rate = Dec::parse("-1").unwrap(),
            1 => r.quotes[0].index_in_quote.as_mut().unwrap().value = Dec::zero(),
            2 => r.quotes[0]
                .book
                .spec
                .tick_bands
                .push((Dec::zero(), Dec::one())),
            3 => r.candidates[0].legs[0].instrument.underlying = "BTC".into(),
            4 => r.scenarios.scenarios[0].probability = Some(Dec::one()),
            _ => r.candidates[0].terminal_fee_native = Some(Dec::parse("-1").unwrap()),
        }
        assert!(inputs::load_analysis(r).is_err(), "case {mutate}");
    }
}
#[test]
fn unknown_candidate_is_preserved() {
    let mut r = request();
    r.candidates[0].template_id = "unknown_future".into();
    assert_eq!(
        inputs::load_analysis(r).unwrap().requests[0].template_id,
        "unknown_future"
    );
}
fn panel() -> PanelEnvelope {
    let r = request();
    PanelEnvelope {
        schema_version: inputs::PANEL_SCHEMA.into(),
        exporter_version: inputs::PANEL_EXPORTER.into(),
        raw_source_integrity: RawSourceIntegrity::RawPinnedBytesVerified,
        projection_trust: ProjectionTrust::ExportedProjectionUnattested,
        pinned_sources: vec![PinnedSource {
            path: "/unread/no/io".into(),
            size_bytes: 1,
            sha256: "0".repeat(64),
        }],
        projection_sha256: options_core::canonical::canonical_hash(&r.quotes).unwrap(),
        quotes: r.quotes,
        gaps: vec![],
    }
}
#[test]
fn panel_tamper_conflict_and_transport() {
    let mut r = request();
    r.fixture_name = None;
    r.quotes.clear();
    let mut p = panel();
    p.quotes[0].book.asks[0].price = Dec::parse("3").unwrap();
    assert!(inputs::apply_panel(r.clone(), p).is_err());
    assert!(inputs::apply_panel(request(), panel()).is_err());
    assert!(inputs::decode_panel(&vec![b' '; inputs::MAX_PANEL_BYTES + 1]).is_err());
    let p = panel();
    assert!(inputs::apply_panel(r, p).is_err());
}
#[test]
fn normalized_panel_keeps_identity_and_unverified_projection() {
    let mut p = panel();
    for q in &mut p.quotes {
        q.book.spec.provenance = Provenance::SealedProxy;
        q.book.spec.exercise_style = ExerciseStyle::Unknown;
        q.book.spec.settlement_rule = SettlementRule::Unknown;
    }
    p.projection_sha256 = options_core::canonical::canonical_hash(&p.quotes).unwrap();
    let original = p.projection_sha256.clone();
    let mut r = request();
    r.fixture_name = None;
    r.candidates[0].legs[0].instrument =
        InstrumentDescriptor::from_spec(&p.quotes[0].book.spec).unwrap();
    for s in &mut r.scenarios.scenarios {
        for point in &mut s.points {
            for (hash, _) in &mut point.delivery_by_spec_hash {
                *hash = r.candidates[0].legs[0].instrument.spec_hash.clone();
            }
        }
    }
    r.quotes.clear();
    let a = inputs::apply_panel(r, p).unwrap();
    assert_eq!(a.quotes[0].book.spec.exercise_style, ExerciseStyle::Unknown);
    assert!(
        a.source_identities
            .contains(&("panel_projection_sha256".into(), original))
    );
    assert!(a.gaps.iter().any(|g| g.status == Status::Unverified));
}
