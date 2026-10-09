//! Pure admission: transport labels never establish account or execution authority.
use crate::{
    canonical::canonical_hash,
    contracts::*,
    decimal::Dec,
    error::{Error, ErrorCode, Result},
};
use std::collections::{BTreeMap, BTreeSet};
pub const PANEL_SCHEMA: &str = "options-workbench-panel.rust.v1";
pub const PANEL_EXPORTER: &str = "options-workbench-exporter.rust.v1";
pub const MAX_PANEL_BYTES: usize = 16 * 1024 * 1024;
fn require(ok: bool, code: ErrorCode) -> Result<()> {
    if ok { Ok(()) } else { Err(Error::new(code)) }
}
fn positive(x: &Dec) -> bool {
    !x.is_negative() && !x.is_zero()
}
fn text(x: &str) -> bool {
    !x.trim().is_empty() && x.len() <= MAX_STRING_BYTES
}
fn clocks(_source: u64, received: u64, available: u64) -> Result<()> {
    require(received <= available, ErrorCode::InvalidTimestamp)
}
fn identities(xs: &[(String, String)]) -> Result<()> {
    let mut ids = BTreeSet::new();
    for (k, v) in xs {
        require(
            text(k) && text(v) && ids.insert(k),
            ErrorCode::InvalidIdentity,
        )?;
    }
    Ok(())
}
pub fn decode_request(bytes: &[u8]) -> Result<Request> {
    let r: Request = crate::codec::decode(bytes, MAX_REQUEST_BYTES)?;
    require(r.schema_version == REQUEST_SCHEMA, ErrorCode::InvalidSchema)?;
    r.validate_limits()?;
    Ok(r)
}
pub fn select_view(views: &[View], id: &str, cutoff: u64) -> Result<View> {
    require(
        !views.is_empty() && views.len() <= MAX_VIEWS,
        ErrorCode::LimitExceeded,
    )?;
    let mut chains: BTreeMap<&str, Vec<&View>> = BTreeMap::new();
    for v in views {
        v.validate()?;
        require(
            text(&v.underlying)
                && text(&v.mechanism)
                && text(&v.report_currency)
                && text(&v.clock_policy),
            ErrorCode::InvalidMetadata,
        )?;
        chains.entry(&v.meta.object_id).or_default().push(v);
    }
    for chain in chains.values_mut() {
        chain.sort_by_key(|v| v.meta.revision);
        for (i, v) in chain.iter().enumerate() {
            require(
                v.meta.revision as usize == i + 1,
                ErrorCode::ConflictingEvidence,
            )?;
            if i > 0 {
                let p = chain[i - 1];
                require(
                    v.meta.parent_hash.as_ref() == Some(&canonical_hash(p)?),
                    ErrorCode::InvalidIdentity,
                )?;
                require(
                    v.meta.cutoff_ns >= p.meta.cutoff_ns
                        && v.meta.received_ns >= p.meta.received_ns
                        && v.meta.available_ns >= p.meta.available_ns,
                    ErrorCode::InvalidTimestamp,
                )?;
            }
        }
    }
    chains
        .get(id)
        .and_then(|c| {
            c.iter().rev().find(|v| {
                v.meta.cutoff_ns <= cutoff
                    && v.meta.received_ns <= cutoff
                    && v.meta.available_ns <= cutoff
            })
        })
        .map(|v| (*v).clone())
        .ok_or(Error::new(ErrorCode::MissingEvidence))
}
fn validate_spec(s: &InstrumentSpec) -> Result<()> {
    require(
        [
            &s.venue,
            &s.instrument_id,
            &s.underlying,
            &s.quote_currency,
            &s.strike_currency,
            &s.settlement_currency,
            &s.margin_currency,
            &s.settlement_index,
            &s.rule_version,
        ]
        .iter()
        .all(|x| text(x))
            && valid_sha256(&s.metadata_hash),
        ErrorCode::InvalidMetadata,
    )?;
    require(
        positive(&s.strike)
            && positive(&s.multiplier)
            && positive(&s.min_quantity_native)
            && positive(&s.quantity_step_native)
            && s.min_quantity_native
                .is_multiple_of(&s.quantity_step_native)
            && s.max_quantity_native.as_ref().is_none_or(|x| {
                x >= &s.min_quantity_native && x.is_multiple_of(&s.quantity_step_native)
            }),
        ErrorCode::InvalidInput,
    )?;
    require(
        s.expiry_ns > s.valid_from_ns
            && s.valid_until_ns.is_none_or(|t| t > s.valid_from_ns)
            && s.settlement_window_ns > 0,
        ErrorCode::InvalidTimestamp,
    )?;
    require(
        !s.tick_bands.is_empty() && s.tick_bands.len() <= MAX_LEVELS && s.tick_bands[0].0.is_zero(),
        ErrorCode::InvalidInput,
    )?;
    for (i, (bound, tick)) in s.tick_bands.iter().enumerate() {
        require(
            !bound.is_negative() && positive(tick) && (i == 0 || bound > &s.tick_bands[i - 1].0),
            ErrorCode::InvalidInput,
        )?;
    }
    Ok(())
}
fn observation(o: &Observation) -> Result<()> {
    clocks(o.source_ns, o.received_ns, o.available_ns)?;
    require(
        positive(&o.value) && text(&o.source_id),
        ErrorCode::InvalidInput,
    )
}
fn validate_quote(q: &QuoteInput, report: &str) -> Result<()> {
    let b = &q.book;
    validate_spec(&b.spec)?;
    clocks(b.source_ns, b.received_ns, b.available_ns)?;
    require(
        text(&q.quote_id)
            && valid_sha256(&b.raw_hash)
            && i128::from(q.timing.minimum_source_age_ns) <= i128::from(q.timing.max_age_ns),
        ErrorCode::InvalidMetadata,
    )?;
    for (levels, bids) in [(&b.bids, true), (&b.asks, false)] {
        require(levels.len() <= MAX_LEVELS, ErrorCode::LimitExceeded)?;
        for (i, l) in levels.iter().enumerate() {
            require(
                !l.price.is_negative() && positive(&l.quantity_native),
                ErrorCode::InvalidInput,
            )?;
            if i > 0 {
                require(
                    if bids {
                        levels[i - 1].price > l.price
                    } else {
                        levels[i - 1].price < l.price
                    },
                    ErrorCode::ConflictingEvidence,
                )?;
            }
        }
    }
    if let (Some(bid), Some(ask)) = (b.bids.first(), b.asks.first()) {
        require(bid.price <= ask.price, ErrorCode::ConflictingEvidence)?;
    }
    if let Some(o) = &q.index_in_quote {
        observation(o)?;
        require(
            q.index_currency.as_ref() == Some(&b.spec.quote_currency),
            ErrorCode::InvalidInput,
        )?;
    } else {
        require(q.index_currency.is_none(), ErrorCode::InvalidInput)?;
    }
    if let Some(o) = &q.quote_to_report {
        observation(o)?;
        require(
            q.fx_pair.as_ref() == Some(&(b.spec.quote_currency.clone(), report.into())),
            ErrorCode::InvalidInput,
        )?;
    } else {
        require(q.fx_pair.is_none(), ErrorCode::InvalidInput)?;
    }
    if let Some(f) = &q.fee_policy {
        require(
            !f.rate.is_negative() && !f.premium_cap.is_negative() && text(&f.fee_currency),
            ErrorCode::InvalidInput,
        )?;
    } else {
        require(q.fee_known_ns.is_none(), ErrorCode::InvalidInput)?;
    }
    identities(&q.source_identities)
}
pub fn load_analysis(r: Request) -> Result<AnalysisInput> {
    crate::canonical::canonical_bytes(&r)?;
    require(r.schema_version == REQUEST_SCHEMA, ErrorCode::InvalidSchema)?;
    r.validate_limits()?;
    let view = select_view(&r.views, &r.view_id, r.cutoff_ns)?;
    r.scenarios.meta.validate(SCENARIOS_SCHEMA)?;
    require(
        r.scenarios.meta.cutoff_ns <= r.cutoff_ns
            && r.scenarios.view_hash == canonical_hash(&view)?,
        ErrorCode::InvalidIdentity,
    )?;
    require(
        text(&r.risk_policy.report_currency)
            && r.risk_policy.report_currency == view.report_currency
            && r.risk_policy.rolling_window_ns > 0
            && [
                &r.risk_policy.per_intent_report,
                &r.risk_policy.active_and_pending_report,
                &r.risk_policy.rolling_tail_paid_report,
            ]
            .iter()
            .all(|x| !x.is_negative())
            && r.risk_policy
                .fx_timing
                .as_ref()
                .is_none_or(|t| i128::from(t.minimum_source_age_ns) <= i128::from(t.max_age_ns)),
        ErrorCode::InvalidInput,
    )?;
    identities(&r.source_identities)?;
    let mut quote_ids = BTreeSet::new();
    let mut evidence = BTreeMap::new();
    let mut specs = BTreeMap::new();
    for q in &r.quotes {
        validate_quote(q, &view.report_currency)?;
        require(
            quote_ids.insert((&q.book.spec.venue, &q.quote_id)),
            ErrorCode::InvalidIdentity,
        )?;
        let hash = canonical_hash(&q.book.spec)?;
        let key = (
            hash.clone(),
            q.book.source_ns,
            q.book.received_ns,
            q.book.available_ns,
        );
        if let Some(prior) = evidence.insert(key, q) {
            require(prior == q, ErrorCode::ConflictingEvidence)?;
        }
        specs.insert(hash, q.book.spec.clone());
    }
    let mut ids = BTreeSet::new();
    for c in &r.candidates {
        require(
            text(&c.request_id)
                && text(&c.template_id)
                && text(&c.template_version)
                && text(&c.exit_policy)
                && ids.insert(&c.request_id)
                && !c.legs.is_empty()
                && c.terminal_fee_native
                    .as_ref()
                    .is_none_or(|f| !f.is_negative()),
            ErrorCode::InvalidInput,
        )?;
        let mut legs = BTreeSet::new();
        for l in &c.legs {
            require(
                positive(&l.quantity_native)
                    && l.instrument.spec_schema == SPEC_SCHEMA
                    && valid_sha256(&l.instrument.spec_hash)
                    && legs.insert((&l.instrument.venue, &l.instrument.instrument_id)),
                ErrorCode::InvalidIdentity,
            )?;
            if let Some(s) = specs.get(&l.instrument.spec_hash) {
                require(
                    l.instrument == InstrumentDescriptor::from_spec(s)?,
                    ErrorCode::InvalidIdentity,
                )?;
            }
        }
    }
    let mut scenario_ids = BTreeSet::new();
    let mut sum = Dec::zero();
    let weighted = r
        .scenarios
        .scenarios
        .iter()
        .any(|s| s.probability.is_some());
    require(!r.scenarios.scenarios.is_empty(), ErrorCode::InvalidInput)?;
    for s in &r.scenarios.scenarios {
        require(
            text(&s.scenario_id)
                && scenario_ids.insert(&s.scenario_id)
                && !s.points.is_empty()
                && s.points.len() <= MAX_SCENARIOS,
            ErrorCode::InvalidInput,
        )?;
        if weighted {
            let p = s
                .probability
                .as_ref()
                .ok_or(Error::new(ErrorCode::InvalidInput))?;
            require(
                !p.is_negative() && p <= &Dec::one(),
                ErrorCode::InvalidInput,
            )?;
            sum = sum.checked_add(p)?;
        }
        for (i, p) in s.points.iter().enumerate() {
            require(
                p.at_ns > view.meta.cutoff_ns
                    && (i == 0 || p.at_ns > s.points[i - 1].at_ns)
                    && p.delivery_by_spec_hash.len() <= MAX_QUOTES
                    && p.liquidity_haircut
                        .as_ref()
                        .is_none_or(|x| !x.is_negative() && x <= &Dec::one()),
                ErrorCode::InvalidInput,
            )?;
            let mut prices = BTreeSet::new();
            for (h, v) in &p.delivery_by_spec_hash {
                require(
                    valid_sha256(h) && prices.insert(h) && !v.is_negative(),
                    ErrorCode::ConflictingEvidence,
                )?;
            }
        }
    }
    if weighted {
        require(
            sum == Dec::one() && r.scenarios.probability_source.is_some(),
            ErrorCode::InvalidInput,
        )?;
    } else {
        require(
            r.scenarios.probability_source.is_none(),
            ErrorCode::InvalidInput,
        )?;
    }
    let mut wallets = BTreeSet::new();
    for b in &r.hypothetical_balances {
        let w = &b.wallet;
        require(
            [&w.venue, &w.account, &w.account_mode, &w.currency]
                .iter()
                .all(|x| text(x))
                && !b.cash.is_negative()
                && wallets.insert((&w.venue, &w.account, &w.account_mode, &w.currency)),
            ErrorCode::InvalidInput,
        )?;
    }
    let mut gaps = vec![StatusResult::new(
        Status::Unverified,
        "source and wallet labels establish no account or execution proof",
    )];
    for c in &r.candidates {
        for l in &c.legs {
            if !specs.contains_key(&l.instrument.spec_hash) {
                gaps.push(StatusResult::new(
                    Status::NotEvaluable,
                    "missing exact descriptor spec evidence",
                ));
            }
        }
    }
    for q in &r.quotes {
        if q.fee_policy.is_none() || q.fee_known_ns.is_none() || q.metadata_known_ns.is_none() {
            gaps.push(StatusResult::new(
                Status::NotEvaluable,
                "missing quote fee or metadata knowledge evidence",
            ));
        }
    }
    Ok(AnalysisInput {
        schema_version: r.schema_version,
        adapter_version: "native_manual.rust.v1".into(),
        cutoff_ns: r.cutoff_ns,
        view,
        view_history: r.views,
        scenarios: r.scenarios,
        requests: r.candidates,
        quotes: r.quotes,
        hypothetical_balances: r.hypothetical_balances,
        risk_policy: r.risk_policy,
        source_identities: r.source_identities,
        gaps,
    })
}
/// Latest causally visible quote; future quote records remain in AnalysisInput.
pub fn select_quote<'a>(
    quotes: &'a [QuoteInput],
    spec_hash: &str,
    cutoff: u64,
) -> Result<Option<&'a QuoteInput>> {
    let mut selected: Option<&QuoteInput> = None;
    for q in quotes {
        if canonical_hash(&q.book.spec)? != spec_hash
            || q.book.received_ns > cutoff
            || q.book.available_ns > cutoff
        {
            continue;
        }
        let key = (q.book.source_ns, q.book.received_ns, q.book.available_ns);
        if let Some(p) = selected
            && key == (p.book.source_ns, p.book.received_ns, p.book.available_ns)
        {
            require(p == q, ErrorCode::ConflictingEvidence)?;
        }
        if selected
            .is_none_or(|p| key > (p.book.source_ns, p.book.received_ns, p.book.available_ns))
        {
            selected = Some(q);
        }
    }
    Ok(selected)
}
pub fn decode_panel(bytes: &[u8]) -> Result<PanelEnvelope> {
    crate::codec::decode(bytes, MAX_PANEL_BYTES)
}
/// Validates projection self-integrity only; caller must independently verify raw pins.
pub fn apply_panel(mut r: Request, p: PanelEnvelope) -> Result<AnalysisInput> {
    require(
        p.schema_version == PANEL_SCHEMA && p.exporter_version == PANEL_EXPORTER,
        ErrorCode::InvalidSchema,
    )?;
    require(
        r.fixture_name.is_none() && r.quotes.is_empty(),
        ErrorCode::ConflictingEvidence,
    )?;
    require(
        !p.pinned_sources.is_empty()
            && p.pinned_sources.len() <= 1024
            && valid_sha256(&p.projection_sha256)
            && p.projection_sha256 == canonical_hash(&p.quotes)?,
        ErrorCode::InvalidIdentity,
    )?;
    let mut paths = BTreeSet::new();
    let mut total = 0u64;
    for pin in &p.pinned_sources {
        require(
            text(&pin.path)
                && paths.insert(&pin.path)
                && valid_sha256(&pin.sha256)
                && pin.size_bytes <= MAX_PANEL_BYTES as u64,
            ErrorCode::InvalidIdentity,
        )?;
        total = total
            .checked_add(pin.size_bytes)
            .ok_or(Error::new(ErrorCode::LimitExceeded))?;
    }
    require(total <= 64 * 1024 * 1024, ErrorCode::LimitExceeded)?;
    let mut quotes = p.quotes;
    for q in &mut quotes {
        q.book.spec.provenance = Provenance::SealedProxy;
        q.book.spec.exercise_style = ExerciseStyle::Unknown;
        q.book.spec.settlement_rule = SettlementRule::Unknown;
        if let Some(o) = &mut q.index_in_quote {
            o.provenance = Provenance::SealedProxy;
        }
        if let Some(o) = &mut q.quote_to_report {
            o.provenance = Provenance::SealedProxy;
        }
        if let Some(f) = &mut q.fee_policy {
            f.provenance = Provenance::SealedProxy;
        }
        q.assumptions
            .push("EXPORTED_PROJECTION_UNATTESTED; raw byte verification belongs to caller".into());
    }
    identities(&r.source_identities)?;
    require(
        !r.source_identities
            .iter()
            .any(|(k, _)| k == "panel_projection_sha256"),
        ErrorCode::ConflictingEvidence,
    )?;
    r.source_identities
        .push(("panel_projection_sha256".into(), p.projection_sha256));
    for c in &r.candidates {
        for l in &c.legs {
            let descriptors = quotes
                .iter()
                .map(|q| InstrumentDescriptor::from_spec(&q.book.spec))
                .collect::<Result<Vec<_>>>()?;
            if descriptors.iter().any(|d| {
                d.venue == l.instrument.venue && d.instrument_id == l.instrument.instrument_id
            }) {
                require(
                    descriptors.contains(&l.instrument),
                    ErrorCode::InvalidIdentity,
                )?;
            }
        }
    }
    let hashes: BTreeSet<String> = quotes
        .iter()
        .map(|q| canonical_hash(&q.book.spec))
        .collect::<Result<_>>()?;
    for scenario in &r.scenarios.scenarios {
        for point in &scenario.points {
            for (hash, _) in &point.delivery_by_spec_hash {
                require(hashes.contains(hash), ErrorCode::InvalidIdentity)?;
            }
        }
    }
    r.quotes = quotes;
    let mut a = load_analysis(r)?;
    a.adapter_version = PANEL_EXPORTER.into();
    a.gaps.extend(p.gaps);
    a.gaps.push(StatusResult::new(
        Status::Unverified,
        "EXPORTED_PROJECTION_UNATTESTED",
    ));
    Ok(a)
}
