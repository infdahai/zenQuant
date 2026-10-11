//! Explicit mathematical inputs only. Not market observations, balances or execution evidence.
//! Financial budgets must be provided by caller; never production defaults.
use crate::{
    canonical::canonical_hash,
    contracts::*,
    decimal::Dec,
    error::{Error, ErrorCode, Result},
};
fn d(s: &str) -> Dec {
    Dec::parse(s).expect("fixed finite mathematical constant")
}

/// Synthetic reservation policy, not a venue fee attestation or real balance snapshot.
pub fn mathematical_reservation_case(
    now: u64,
    initial: Dec,
) -> Result<(crate::reservations::Funding, crate::reservations::Request)> {
    use crate::{
        canonical::bytes_sha256,
        intents::*,
        reservations::{self as r, *},
    };
    let end = now
        .checked_add(3_600_000_000_000)
        .ok_or(Error::new(ErrorCode::InvalidTimestamp))?;
    let mut spec = mathematical_spec(OptionType::C, d("100"));
    spec.expiry_ns = end;
    spec.valid_from_ns = now;
    spec.valid_until_ns = Some(end);
    let owner = Owner {
        venue: spec.venue.clone(),
        simulated_account_id: "mathematical-cash-account".into(),
        subaccount: Subaccount::Main,
        collateral_domain: CollateralDomain::FullyPaidSingleCurrencyCash,
        currency: "USDC".into(),
    };
    let funding = Funding {
        schema_version: FUNDING_SCHEMA.into(),
        mode: Mode::Simulated,
        snapshot_id: "explicit-mathematical-funding".into(),
        owner: owner.clone(),
        initial_balance: initial,
        available_ns: now,
        source_ref: "EXPLICIT_SYNTHETIC_BALANCE_NOT_REAL_ACCOUNT".into(),
        source_sha256: bytes_sha256(b"explicit synthetic balance assumption"),
        fee_cap: FeeCap {
            schema_version: FEE_SCHEMA.into(),
            premium_fraction: d(".125"),
            known_ns: now,
            valid_from_ns: now,
            valid_until_ns: end,
            source_ref: "SYNTHETIC_ALL_OPENING_FEES_LE_0.125_TIMES_PREMIUM_NOT_VENUE_VERIFIED"
                .into(),
            source_sha256: bytes_sha256(b"synthetic all opening fees <= premium * 0.125"),
        },
        max_spec_age_ns: 3_600_000_000_000,
    };
    let intent = Intent {
        schema_version: crate::intents::REQUEST_SCHEMA.into(),
        mode: Mode::Simulated,
        request_id: "mathematical-reservation-1".into(),
        simulated_account_id: owner.simulated_account_id.clone(),
        source: DecisionSource {
            kind: DecisionKind::Manual,
            source_id: "synthetic-reservation-example".into(),
            source_version: "v1".into(),
            evidence_sha256: bytes_sha256(b"explicit synthetic limit BUY request"),
            evidence_available_ns: now,
        },
        evidence_cutoff_ns: now,
        created_ns: now,
        expires_ns: end,
        metadata_known_ns: now,
        leg: CandidateLeg {
            instrument: InstrumentDescriptor::from_spec(&spec)?,
            side: Side::Buy,
            quantity_native: d(".01"),
        },
        spec,
        limit_price_native: d("2"),
    };
    let request = r::Request {
        schema_version: r::REQUEST_SCHEMA.into(),
        mode: Mode::Simulated,
        owner,
        funding_sha256: canonical_hash(&funding)?,
        intent_sha256: canonical_hash(&intent)?,
        intent,
    };
    Ok((funding, request))
}
pub fn mathematical_spec(option_type: OptionType, strike: Dec) -> InstrumentSpec {
    InstrumentSpec {
        venue: "MATHEMATICAL_FIXTURE".into(),
        instrument_id: format!("ETH-1000-{strike}-{option_type:?}"),
        product: Product::LinearOption,
        underlying: "ETH".into(),
        quote_currency: "USDC".into(),
        strike_currency: "USDC".into(),
        settlement_currency: "USDC".into(),
        margin_currency: "USDC".into(),
        expiry_ns: 1000,
        strike,
        option_type,
        exercise_style: ExerciseStyle::European,
        quantity_unit: QuantityUnit::Base,
        multiplier: d("1"),
        price_unit: PriceUnit::QuotePerBase,
        min_quantity_native: d(".01"),
        quantity_step_native: d(".01"),
        max_quantity_native: None,
        tick_bands: vec![(d("0"), d(".01"))],
        settlement_index: "MATHEMATICAL_ETH".into(),
        settlement_window_ns: 1,
        settlement_rule: SettlementRule::LinearCash,
        rule_version: "mathematical.v1".into(),
        metadata_hash: "0".repeat(64),
        valid_from_ns: 0,
        valid_until_ns: None,
        provenance: Provenance::Fixture,
    }
}
pub fn mathematical_meta(schema: &str) -> RecordMeta {
    RecordMeta {
        object_id: "mathematical-view".into(),
        revision: 1,
        parent_hash: None,
        schema_version: schema.into(),
        method_version: ARITHMETIC_METHOD.into(),
        received_ns: 10,
        available_ns: 10,
        cutoff_ns: 10,
        source_ref: "mathematical_fixture_not_market_data".into(),
        source_sha256: None,
        provenance: Provenance::Fixture,
    }
}
pub fn mathematical_view(capital: Dec, loss_budget: Dec) -> Result<View> {
    let view = View {
        meta: mathematical_meta(VIEW_SCHEMA),
        underlying: "ETH".into(),
        mechanism: "mathematical_terminal_payoff".into(),
        direction: Direction::Unspecified,
        horizon_ns: 1000,
        report_currency: "USDC".into(),
        initial_capital: capital,
        max_loss_budget: loss_budget,
        competing_explanations: vec![],
        invalidation: vec![],
        no_trade_conditions: vec![],
        clock_policy: "manual_unattested".into(),
    };
    view.validate()?;
    Ok(view)
}
pub fn mathematical_quote(spec: InstrumentSpec, ask: Dec, bid: Dec) -> QuoteInput {
    let obs = |value| Observation {
        value,
        source_ns: 10,
        received_ns: 10,
        available_ns: 10,
        source_id: "mathematical_fixture_not_market_data".into(),
        provenance: Provenance::Fixture,
    };
    QuoteInput {
        quote_id: spec.instrument_id.clone(),
        book: Book {
            spec,
            bids: vec![BookLevel {
                price: bid,
                quantity_native: d("1"),
            }],
            asks: vec![BookLevel {
                price: ask,
                quantity_native: d("1"),
            }],
            source_ns: 10,
            received_ns: 10,
            available_ns: 10,
            raw_hash: "0".repeat(64),
        },
        index_in_quote: Some(obs(d("100"))),
        index_currency: Some("USDC".into()),
        quote_to_report: Some(obs(d("1"))),
        fx_pair: Some(("USDC".into(), "USDC".into())),
        fee_policy: Some(FeePolicy {
            rate: d(".0003"),
            premium_cap: d(".125"),
            fee_currency: "USDC".into(),
            provenance: Provenance::Fixture,
        }),
        fee_known_ns: Some(0),
        metadata_known_ns: Some(0),
        timing: TimingPolicy {
            max_age_ns: 100,
            minimum_source_age_ns: 0,
        },
        source_identities: vec![],
        assumptions: vec!["MATHEMATICAL_FIXTURE_NOT_MARKET_DATA".into()],
    }
}
/// Explicit finite fixture: caller supplies capital, loss, wallets and all policy budgets.
pub fn mathematical_request(
    name: &str,
    capital: Dec,
    loss_budget: Dec,
    balances: Vec<WalletBalance>,
    risk_policy: RiskPolicy,
) -> Result<Request> {
    let view = mathematical_view(capital, loss_budget)?;
    let mut quotes = vec![mathematical_quote(
        mathematical_spec(OptionType::C, d("100")),
        d("2"),
        d("1.9"),
    )];
    let template = match name {
        "terminal-eth-call" => "long_call",
        "terminal-eth-straddle" => {
            quotes.push(mathematical_quote(
                mathematical_spec(OptionType::P, d("100")),
                d("1.5"),
                d("1.4"),
            ));
            "long_straddle"
        }
        "terminal-vertical" => {
            quotes[0] =
                mathematical_quote(mathematical_spec(OptionType::C, d("100")), d("4"), d("3.9"));
            quotes.push(mathematical_quote(
                mathematical_spec(OptionType::C, d("110")),
                d("5.1"),
                d("5"),
            ));
            "call_vertical"
        }
        _ => return Err(Error::new(ErrorCode::Unsupported)),
    };
    let legs = quotes
        .iter()
        .enumerate()
        .map(|(i, q)| {
            Ok(CandidateLeg {
                instrument: InstrumentDescriptor::from_spec(&q.book.spec)?,
                side: if name == "terminal-vertical" && i == 1 {
                    Side::Sell
                } else {
                    Side::Buy
                },
                quantity_native: d(".01"),
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let scenarios = ["90", "100", "110"]
        .iter()
        .map(|s| {
            Ok(Scenario {
                scenario_id: format!("mathematical-{s}"),
                points: vec![ScenarioPoint {
                    at_ns: 1000,
                    delivery_by_spec_hash: quotes
                        .iter()
                        .map(|q| Ok((canonical_hash(&q.book.spec)?, d(s))))
                        .collect::<Result<Vec<_>>>()?,
                    iv_shift: None,
                    liquidity_haircut: None,
                }],
                probability: None,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(Request {
        schema_version: REQUEST_SCHEMA.into(),
        cutoff_ns: 10,
        view_id: view.meta.object_id.clone(),
        scenarios: ScenarioSet {
            meta: RecordMeta {
                object_id: "mathematical-scenarios".into(),
                ..mathematical_meta(SCENARIOS_SCHEMA)
            },
            view_hash: canonical_hash(&view)?,
            purpose: ScenarioPurpose::Forecast,
            probability_source: None,
            scenarios,
        },
        views: vec![view],
        candidates: vec![CandidateRequest {
            request_id: "mathematical-candidate".into(),
            template_id: template.into(),
            template_version: "v1".into(),
            legs,
            exit_policy: "expiry_only".into(),
            terminal_fee_native: Some(d("0")),
            sleeve: Sleeve::Directional,
        }],
        quotes,
        hypothetical_balances: balances,
        risk_policy,
        source_identities: vec![],
        fixture_name: Some(name.into()),
    })
}
