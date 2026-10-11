//! Business invariants: no files, locks, processes or adapter implementation.
use options_core::{
    canonical::{bytes_sha256, canonical_bytes, canonical_hash},
    codec,
    decimal::Dec,
    fixtures::mathematical_reservation_case,
    intents::{DecisionKind, IdentityMatch, compare_identity},
    reservations::{Authority, CashFailure, Funding, Outcome, Request},
};
fn d(s: &str) -> Dec {
    Dec::parse(s).unwrap()
}
fn case() -> (Funding, Request) {
    mathematical_reservation_case(100, d(".045")).unwrap()
}
fn request(template: &Request, id: &str) -> Request {
    let mut r = template.clone();
    r.intent.request_id = id.into();
    r.intent_sha256 = canonical_hash(&r.intent).unwrap();
    r
}
fn raw(r: &Request) -> String {
    bytes_sha256(&canonical_bytes(r).unwrap())
}
fn roundtrip(s: &Authority) -> Authority {
    codec::decode(&canonical_bytes(s).unwrap(), 16 * 1024 * 1024).unwrap()
}
#[test]
fn immutable_transitions_conserve_shared_manual_and_algorithm_budget() {
    let (f, template) = case();
    let empty = Authority::new(100);
    let funded = empty.fund(&f, 100).unwrap();
    assert!(empty.balances().unwrap().is_empty());
    assert_eq!(funded.outcome, Outcome::Funded);
    let mut state = funded.next;
    for id in ["manual", "algorithm"] {
        let mut r = request(&template, id);
        if id == "algorithm" {
            r.intent.source.kind = DecisionKind::Algorithm;
        }
        r.intent_sha256 = canonical_hash(&r.intent).unwrap();
        let next = state.reserve(&r, &raw(&r), 101).unwrap();
        assert_eq!(next.next.records().len(), state.records().len() + 1);
        assert!(matches!(next.outcome, Outcome::Reserved(_)));
        state = next.next;
        let b = &state.balances().unwrap()[0];
        assert_eq!(
            b.available.checked_add(&b.active_reserved).unwrap(),
            f.initial_balance
        );
        assert!(!b.available.is_negative());
        assert_eq!(roundtrip(&state), state);
    }
    let old = state.clone();
    let third = request(&template, "third");
    assert_eq!(
        state.reserve(&third, &raw(&third), 102).unwrap_err(),
        CashFailure::InsufficientFunds
    );
    assert_eq!(state, old);
    assert_eq!(state.balances().unwrap()[0].available, Dec::zero());
}
#[test]
fn funding_and_request_identity_cannot_reinject_or_double_debit() {
    let (f, r) = case();
    let state = Authority::new(100).fund(&f, 100).unwrap().next;
    let duplicate = state.fund(&f, 101).unwrap();
    assert_eq!(duplicate.outcome, Outcome::AlreadyFunded);
    assert_eq!(duplicate.next, state);
    let mut changed = f.clone();
    changed.snapshot_id = "new-budget-name".into();
    assert_eq!(
        state.fund(&changed, 101).unwrap_err(),
        CashFailure::FundingConflict
    );
    let first = state.reserve(&r, &raw(&r), 101).unwrap();
    let recovered = roundtrip(&first.next);
    let retry = recovered
        .reserve(&r, &bytes_sha256(b"different transport whitespace"), 102)
        .unwrap();
    assert!(matches!(retry.outcome, Outcome::AlreadyReserved(_)));
    assert_eq!(retry.outcome.record(), first.outcome.record());
    assert_eq!(retry.next, first.next);
    let mut changed = r.clone();
    changed.intent.limit_price_native = d("3");
    changed.intent_sha256 = canonical_hash(&changed.intent).unwrap();
    assert_eq!(
        recovered
            .reserve(&changed, &raw(&changed), 102)
            .unwrap_err(),
        CashFailure::RequestConflict
    );
    assert_eq!(
        compare_identity(&r.intent, &r.intent),
        IdentityMatch::SameInput
    );
    assert_eq!(
        compare_identity(&r.intent, &changed.intent),
        IdentityMatch::Conflict
    );
    assert_eq!(
        compare_identity(&r.intent, &request(&r, "other").intent),
        IdentityMatch::DifferentId
    );
}
#[test]
fn historical_holds_never_expire_and_clock_or_input_failure_never_changes_state() {
    let (f, r) = case();
    let state = Authority::new(100)
        .fund(&f, 100)
        .unwrap()
        .next
        .reserve(&r, &raw(&r), 101)
        .unwrap()
        .next;
    let old = state.clone();
    assert_eq!(
        state.reserve(&r, &raw(&r), 100).unwrap_err(),
        CashFailure::ClockRegression
    );
    assert_eq!(
        state.reserve(&r, "not-a-hash", 102).unwrap_err(),
        CashFailure::InvalidInput
    );
    let late = r.intent.expires_ns + 1;
    let retry = state.reserve(&r, &raw(&r), late).unwrap();
    assert_eq!(retry.next, state);
    let new = request(&r, "new");
    assert!(matches!(
        state.reserve(&new, &raw(&new), late),
        Err(CashFailure::NotEvaluable(_))
    ));
    assert_eq!(state, old);
    assert_eq!(state.balances().unwrap()[0].active_reserved, d(".0225"));
}
#[test]
fn restored_state_revalidates_business_invariants_independent_of_envelope_hash() {
    let (f, r) = case();
    let state = Authority::new(100)
        .fund(&f, 100)
        .unwrap()
        .next
        .reserve(&r, &raw(&r), 101)
        .unwrap()
        .next;
    for variant in 0..4 {
        let mut v = serde_json::to_value(&state).unwrap();
        match variant {
            0 => v["reservations"][0]["bounds"]["total"] = "0".into(),
            1 => v["last_admission_ns"] = 0.into(),
            2 => v["fundings"][0]["funding"]["initial_balance"] = "1000".into(),
            _ => {
                let duplicate = v["reservations"][0].clone();
                v["reservations"].as_array_mut().unwrap().push(duplicate);
            }
        }
        assert!(serde_json::from_value::<Authority>(v).is_err());
    }
    assert_eq!(roundtrip(&state), state);
}

#[test]
fn results_outside_v1_persisted_decimal_precision_refuse_before_state_changes() {
    use options_core::{contracts::InstrumentDescriptor, reservations::EvaluationFailure};
    let (f, mut r) = case();
    let tiny = d("0.0000000000000000000000000001");
    r.intent.spec.min_quantity_native = tiny.clone();
    r.intent.spec.quantity_step_native = tiny.clone();
    r.intent.leg.quantity_native = tiny;
    r.intent.limit_price_native = d(".1");
    r.intent.leg.instrument = InstrumentDescriptor::from_spec(&r.intent.spec).unwrap();
    r.intent_sha256 = canonical_hash(&r.intent).unwrap();
    let state = Authority::new(100).fund(&f, 100).unwrap().next;
    let original = state.clone();
    assert_eq!(
        state.reserve(&r, &raw(&r), 101).unwrap_err(),
        CashFailure::NotEvaluable(EvaluationFailure::NumericalLimitation)
    );
    assert_eq!(state, original);
}
