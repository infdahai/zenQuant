use options_core::{
    canonical::{canonical_bytes, canonical_hash},
    codec,
    paper::*,
};
use serde_json::{Value, json};
const NOW: u64 = 1_800_000_000_000;
fn settings() -> Command {
    codec::decode(
        include_bytes!("../../options-workbench/fixtures/manual-paper-settings.json"),
        10000,
    )
    .unwrap()
}
fn intent() -> Command {
    codec::decode(
        include_bytes!("../../options-workbench/fixtures/manual-paper-intent.json"),
        10000,
    )
    .unwrap()
}
fn modified(c: Command, change: impl FnOnce(&mut Value)) -> Command {
    let mut v = serde_json::to_value(c).unwrap();
    change(&mut v);
    codec::decode(&serde_json::to_vec(&v).unwrap(), 10000).unwrap()
}
fn submit(j: &Journal, c: Command, now: u64) -> Journal {
    let preview = j.preview(c, now).unwrap();
    j.confirm(
        &Confirmation {
            preview,
            acknowledged_record_only: true,
        },
        now,
    )
    .unwrap()
    .0
}
fn configured() -> Journal {
    submit(&Journal::default(), settings(), NOW)
}
#[test]
fn allocation_is_one_exact_budget_not_two_balances() {
    let j = configured();
    let v = j.view().unwrap();
    assert_eq!(v.unallocated_usd.to_string(), "10000");
    assert_eq!(v.total_budget_usd.to_string(), "100000");
    assert_eq!(v.execution_authority, "NONE");
    for amount in ["40000.0000000000000000000000000001", "100000"] {
        let c = modified(settings(), |v| {
            v["action"]["value"]["binance_usdt"]["allocation"]["allocation_usd"] = amount.into()
        });
        assert_eq!(
            Journal::default().preview(c, NOW).unwrap_err(),
            Failure::OverAllocated
        );
    }
    let c = modified(settings(), |v| {
        v["action"]["value"]["deribit_usdc"] = Value::Null;
        v["action"]["value"]["binance_usdt"] = Value::Null;
    });
    assert_eq!(
        submit(&Journal::default(), c, NOW)
            .view()
            .unwrap()
            .unallocated_usd
            .to_string(),
        "100000"
    );
}
#[test]
fn no_conversion_margin_or_leverage_defaults() {
    for key in ["conversion", "confirmed"] {
        let c = modified(settings(), |v| {
            let a = &mut v["action"]["value"]["deribit_usdc"];
            if key == "conversion" {
                a[key] = Value::Null;
            } else {
                a["conversion"][key] = false.into();
            }
        });
        assert_eq!(
            Journal::default().preview(c, NOW).unwrap_err(),
            Failure::ConversionUnconfirmed
        );
    }
    for key in ["margin_mode", "max_leverage"] {
        let c = modified(settings(), |v| {
            v["action"]["value"]["binance_usdt"][key] = Value::Null
        });
        assert_eq!(
            Journal::default().preview(c, NOW).unwrap_err(),
            Failure::RiskSettingsMissing
        );
    }
}
#[test]
fn preview_is_pure_confirmation_binds_parameters_evidence_and_revision() {
    let j = configured();
    let before = canonical_bytes(&j).unwrap();
    let p = j.preview(intent(), NOW + 1).unwrap();
    assert_eq!(canonical_bytes(&j).unwrap(), before);
    let mut changed = p.clone();
    if let Action::Record(i) = &mut changed.command.action {
        i.requested_budget_usd = "200".parse().unwrap();
    }
    assert_eq!(
        j.confirm(
            &Confirmation {
                preview: changed,
                acknowledged_record_only: true
            },
            NOW + 2
        )
        .unwrap_err(),
        Failure::InvalidPreview
    );
    let mut changed = p.clone();
    if let Action::Record(i) = &mut changed.command.action {
        i.evidence.fees = Some(EvidenceRef {
            sha256: "a".repeat(64),
            source: "engineering-only".into(),
            kind: EvidenceKind::Synthetic,
            observed_ms: NOW,
            available_ms: NOW,
            valid_until_ms: NOW + 1000,
        });
    }
    assert_eq!(
        j.confirm(
            &Confirmation {
                preview: changed,
                acknowledged_record_only: true
            },
            NOW + 2
        )
        .unwrap_err(),
        Failure::InvalidPreview
    );
    let j2 = submit(
        &j,
        modified(settings(), |v| v["request_id"] = "reconfigure".into()),
        NOW + 2,
    );
    assert_eq!(
        j2.confirm(
            &Confirmation {
                preview: p.clone(),
                acknowledged_record_only: true
            },
            NOW + 3
        )
        .unwrap_err(),
        Failure::StalePreview
    );
    assert_eq!(
        j.confirm(
            &Confirmation {
                preview: p.clone(),
                acknowledged_record_only: true
            },
            p.expires_ms
        )
        .unwrap_err(),
        Failure::StalePreview
    );
    assert_eq!(
        j.confirm(
            &Confirmation {
                preview: p,
                acknowledged_record_only: false
            },
            NOW + 2
        )
        .unwrap_err(),
        Failure::InvalidInput
    );
}
#[test]
fn missing_fees_and_margin_never_grant_execution_permission() {
    let j = configured();
    let c = intent();
    if let Action::Record(i) = &c.action {
        let b = i.blockers(NOW);
        assert!(b.contains(&"FEES_MISSING".into()));
        assert!(b.contains(&"MARGIN_MISSING".into()));
        assert!(b.contains(&"NO_ORDER_OR_FILL_ENGINE".into()));
    }
    let j = submit(&j, c, NOW + 1);
    let v = j.view().unwrap();
    assert_eq!(v.intents[0].status, "RECORDED_UNASSESSED");
    assert_eq!(v.unallocated_usd.to_string(), "10000");
    assert_eq!(
        j.preview(
            modified(intent(), |v| {
                v["request_id"] = "too-big".into();
                v["action"]["value"]["requested_budget_usd"] = "1000.00000001".into();
            }),
            NOW + 2
        )
        .unwrap_err(),
        Failure::SingleIntentLimit
    );
}
#[test]
fn replay_and_withdraw_never_duplicate_or_reactivate() {
    let j = configured();
    let p = j.preview(intent(), NOW + 1).unwrap();
    let confirmation = Confirmation {
        preview: p,
        acknowledged_record_only: true,
    };
    let (j, replay) = j.confirm(&confirmation, NOW + 2).unwrap();
    assert!(!replay);
    let (same, replay) = j.confirm(&confirmation, NOW + PREVIEW_TTL_MS + 10).unwrap();
    assert!(replay);
    assert_eq!(j, same);
    let alternate = configured().preview(intent(), NOW + 2).unwrap();
    let (same, replay) = j
        .confirm(
            &Confirmation {
                preview: alternate,
                acknowledged_record_only: true,
            },
            NOW + 3,
        )
        .unwrap();
    assert!(replay);
    assert_eq!(j, same);
    let v = j.view().unwrap();
    let w = Command {
        schema_version: SCHEMA.into(),
        request_id: "withdraw".into(),
        action: Action::Withdraw(Withdrawal {
            intent_id: v.intents[0].intent.intent_id.clone(),
            intent_sha256: v.intents[0].intent_sha256.clone(),
        }),
    };
    let j = submit(&j, w.clone(), NOW + 3);
    let v = j.view().unwrap();
    assert_eq!(v.intents[0].status, "WITHDRAWN_RECORD_ONLY");
    assert_eq!(v.unallocated_usd.to_string(), "10000");
    let replay = j.confirm(&confirmation, NOW + 4).unwrap();
    assert!(replay.1);
    assert_eq!(replay.0, j);
    assert_eq!(j.preview(intent(), NOW + 5).unwrap(), confirmation.preview);
    let reopened: Journal = codec::decode(&canonical_bytes(&j).unwrap(), 100000).unwrap();
    assert_eq!(reopened.view().unwrap(), v);
    assert_eq!(
        j.preview(
            modified(intent(), |v| v["action"]["value"]["requested_budget_usd"] =
                "200".into()),
            NOW + 5
        )
        .unwrap_err(),
        Failure::Conflict
    );
    assert_eq!(
        j.preview(
            modified(intent(), |v| v["request_id"] = "second-id".into()),
            NOW + 5
        )
        .unwrap_err(),
        Failure::Conflict
    );
}
#[test]
fn product_is_strict_and_unverified_evidence_remains_blocked() {
    let j = configured();
    for change in 0..4 {
        let c = modified(intent(), |v| {
            let p = &mut v["action"]["value"]["instrument"]["details"];
            match change {
                0 => p["symbol"] = "BTCUSD_PERP".into(),
                1 => p["quantity_btc"] = "0".into(),
                2 => p["leverage"] = "3".into(),
                _ => p["margin_mode"] = "CROSS".into(),
            };
        });
        assert!(j.preview(c, NOW).is_err());
    }
    let mut v = serde_json::to_value(intent()).unwrap();
    v["action"]["value"]["instrument"]["details"]["expiry_ms"] = json!(NOW);
    assert!(codec::decode::<Command>(&serde_json::to_vec(&v).unwrap(), 10000).is_err());
    let c = modified(
        intent(),
        |v| v["action"]["value"]["evidence"]["fees"] = json!({"sha256":"a".repeat(64),"source":"engineering-only","kind":"SYNTHETIC_ENGINEERING_ONLY","observed_ms":NOW-2,"available_ms":NOW-1,"valid_until_ms":NOW+2}),
    );
    if let Action::Record(i) = &c.action {
        assert!(i.blockers(NOW).contains(&"FEES_UNVERIFIED".into()));
        assert!(i.blockers(NOW + 2).contains(&"FEES_STALE".into()));
    }
    assert!(j.preview(c, NOW).is_ok());
    let c = modified(
        intent(),
        |v| v["action"]["value"]["evidence"]["fees"] = json!({"sha256":"a".repeat(64),"source":"future","kind":"USER_SUPPLIED_UNVERIFIED","observed_ms":NOW,"available_ms":NOW+1,"valid_until_ms":NOW+2}),
    );
    assert!(j.preview(c, NOW).is_err());
}
#[test]
fn option_identity_requires_manual_contract_and_old_contracts_stay_strict() {
    let c = modified(
        intent(),
        |v| v["action"]["value"]["instrument"] = json!({"product":"DERIBIT_LINEAR_USDC_OPTION_BUY","details":{"schema_version":"manual-deribit-option.rust.v1","symbol":"BTC_USDC-29JAN27-60000-C","underlying":"BTC","expiry_ms":NOW+1000000,"option_type":"C","strike_usdc":"60000","quantity_contracts":"1","limit_usdc_per_base":"1500"}}),
    );
    assert!(configured().preview(c.clone(), NOW).is_ok());
    let bad = modified(c, |v| {
        v["action"]["value"]["instrument"]["details"]["option_type"] = "P".into()
    });
    assert!(configured().preview(bad, NOW).is_err());
    assert!(
        codec::decode::<options_core::intents::Intent>(&canonical_bytes(&intent()).unwrap(), 10000)
            .is_err()
    );
    let old = include_bytes!("../../options-workbench/fixtures/simulated-intent.json");
    assert!(codec::decode::<options_core::intents::Intent>(old, 100000).is_ok());
    assert!(codec::decode::<Command>(old, 100000).is_err());
}
#[test]
fn journal_rejects_rehashed_invalid_history_and_duplicate_ids() {
    let j = submit(&configured(), intent(), NOW + 1);
    let mut v = serde_json::to_value(&j).unwrap();
    v["entries"][1]["confirmed_ms"] = json!(NOW - 1);
    let bad: Journal = codec::decode(&serde_json::to_vec(&v).unwrap(), 100000).unwrap();
    assert_eq!(bad.validate().unwrap_err(), Failure::Corrupt);
    let mut v = serde_json::to_value(&j).unwrap();
    let first = v["entries"][0].clone();
    v["entries"].as_array_mut().unwrap().push(first);
    let bad: Journal = codec::decode(&serde_json::to_vec(&v).unwrap(), 100000).unwrap();
    assert!(bad.view().is_err());
    assert_ne!(canonical_hash(&bad).unwrap(), canonical_hash(&j).unwrap());
}
