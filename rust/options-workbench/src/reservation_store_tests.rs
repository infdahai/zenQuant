use super::*;
use options_core::{
    decimal::Dec,
    reservations::{Funding, Request},
};
use options_core::{fixtures::mathematical_reservation_case, intents::DecisionKind};
use std::{
    fs,
    process::{Command, Stdio},
    sync::{
        Arc, Barrier,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    },
};
const NOW: u64 = 1_000_000_000;
fn d(s: &str) -> Dec {
    Dec::parse(s).unwrap()
}
struct Temp {
    root: PathBuf,
    funding: Funding,
    request: Request,
}
impl Temp {
    fn new(balance: &str) -> Self {
        static NEXT: AtomicUsize = AtomicUsize::new(0);
        let root = fs::canonicalize(std::env::temp_dir())
            .unwrap()
            .join(format!(
                "risk-store-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
        fs::create_dir(&root).unwrap();
        let (funding, request) = mathematical_reservation_case(NOW, d(balance)).unwrap();
        let t = Self {
            root,
            funding,
            request,
        };
        initialize_at(&t.path(), || Ok(NOW)).unwrap();
        t
    }
    fn path(&self) -> PathBuf {
        self.root.join("authority")
    }
    fn fund(&self) {
        fund_at(
            &self.path(),
            &canonical_bytes(&self.funding).unwrap(),
            || Ok(NOW),
            Hooks::default(),
        )
        .unwrap();
    }
    fn input(&self, id: &str) -> Vec<u8> {
        let mut r = self.request.clone();
        r.intent.request_id = id.into();
        r.intent_sha256 = canonical_hash(&r.intent).unwrap();
        canonical_bytes(&r).unwrap()
    }
    fn reserve(&self, id: &str) -> Receipt {
        reserve_at(&self.path(), &self.input(id), || Ok(NOW), Hooks::default())
            .unwrap()
            .0
    }
    fn replay(&self) -> Receipt {
        replay_at(&self.path(), || Ok(NOW)).unwrap()
    }
    fn bytes(&self) -> Vec<u8> {
        fs::read(self.path().join("snapshot.json")).unwrap()
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}
fn conserved(receipt: &Receipt) {
    for b in &receipt.balances {
        assert_eq!(
            b.available.checked_add(&b.active_reserved).unwrap(),
            b.initial
        );
        assert!(!b.available.is_negative());
    }
}

#[test]
fn same_owner_cannot_split_or_reinject_its_initial_cash() {
    let t = Temp::new(".045");
    t.fund();
    let original = t.bytes();
    let repeated = fund_at(
        &t.path(),
        &canonical_bytes(&t.funding).unwrap(),
        || Ok(NOW + 1),
        Hooks::default(),
    )
    .unwrap();
    assert_eq!(repeated.result, "ALREADY_FUNDED");
    assert_eq!(t.bytes(), original);
    for variant in 0..3 {
        let mut f = t.funding.clone();
        match variant {
            0 => f.snapshot_id = "another-budget".into(),
            1 => f.initial_balance = d("100"),
            _ => f.fee_cap.premium_fraction = d(".01"),
        }
        assert_eq!(
            fund_at(
                &t.path(),
                &canonical_bytes(&f).unwrap(),
                || Ok(NOW),
                Hooks::default()
            )
            .unwrap_err(),
            Failure::FundingConflict
        );
        assert_eq!(t.bytes(), original);
    }
    let a = t.reserve("manual");
    conserved(&a);
    let mut algorithm: Request = core::decode_request(&t.input("algorithm")).unwrap();
    algorithm.intent.source.kind = DecisionKind::Algorithm;
    algorithm.intent_sha256 = canonical_hash(&algorithm.intent).unwrap();
    let b = reserve_at(
        &t.path(),
        &canonical_bytes(&algorithm).unwrap(),
        || Ok(NOW),
        Hooks::default(),
    )
    .unwrap()
    .0;
    conserved(&b);
    assert_eq!(b.balances[0].available, Dec::zero());
    assert_eq!(b.balances[0].active_reserved, d(".045"));
    let full = t.bytes();
    assert_eq!(
        reserve_at(&t.path(), &t.input("third"), || Ok(NOW), Hooks::default()).unwrap_err(),
        Failure::InsufficientFunds
    );
    assert_eq!(t.bytes(), full);
}

#[test]
fn repeated_input_is_idempotent_but_changed_input_conflicts_without_partial_update() {
    let t = Temp::new("1");
    t.fund();
    t.reserve("one");
    let original = t.bytes();
    let mut same = t.input("one");
    same.push(b'\n');
    let r = reserve_at(&t.path(), &same, || Ok(NOW + 1), Hooks::default())
        .unwrap()
        .0;
    assert_eq!(r.result, "ALREADY_RESERVED");
    assert_eq!(r.reservation_count, 1);
    assert_eq!(t.bytes(), original);
    let mut changed = core::decode_request(&same).unwrap();
    changed.intent.limit_price_native = d("3");
    changed.intent_sha256 = canonical_hash(&changed.intent).unwrap();
    assert_eq!(
        reserve_at(
            &t.path(),
            &canonical_bytes(&changed).unwrap(),
            || Ok(NOW),
            Hooks::default()
        )
        .unwrap_err(),
        Failure::RequestConflict
    );
    assert_eq!(
        reserve_at(&t.path(), b"{}", || Ok(NOW), Hooks::default()).unwrap_err(),
        Failure::InvalidInput
    );
    assert_eq!(t.bytes(), original);
    assert!(!t.path().join("pending.json").exists());
    assert_eq!(r.execution_authority, "NONE");
    assert_eq!(r.scope, "SIMULATED_OPENING_PREMIUM_AND_FEE_ONLY");
}

#[test]
fn different_options_share_cash_while_distinct_explicit_owners_remain_separate() {
    use options_core::contracts::{InstrumentDescriptor, OptionType};
    let t = Temp::new(".045");
    t.fund();
    t.reserve("first-option");
    let mut r = core::decode_request(&t.input("second-option")).unwrap();
    r.intent.spec.option_type = OptionType::P;
    r.intent.spec.instrument_id = "MATHEMATICAL_SECOND_OPTION".into();
    r.intent.leg.instrument = InstrumentDescriptor::from_spec(&r.intent.spec).unwrap();
    r.intent_sha256 = canonical_hash(&r.intent).unwrap();
    let second = reserve_at(
        &t.path(),
        &canonical_bytes(&r).unwrap(),
        || Ok(NOW),
        Hooks::default(),
    )
    .unwrap()
    .0;
    assert_eq!(second.balances.len(), 1);
    assert_eq!(second.balances[0].available, Dec::zero());
    conserved(&second);
    let mut f = t.funding.clone();
    f.owner.simulated_account_id = "independent-simulated-account".into();
    f.initial_balance = d(".1");
    fund_at(
        &t.path(),
        &canonical_bytes(&f).unwrap(),
        || Ok(NOW),
        Hooks::default(),
    )
    .unwrap();
    r.intent.request_id = "independent-owner".into();
    r.owner = f.owner.clone();
    r.intent.simulated_account_id = f.owner.simulated_account_id.clone();
    r.intent_sha256 = canonical_hash(&r.intent).unwrap();
    r.funding_sha256 = canonical_hash(&f).unwrap();
    let other = reserve_at(
        &t.path(),
        &canonical_bytes(&r).unwrap(),
        || Ok(NOW),
        Hooks::default(),
    )
    .unwrap()
    .0;
    conserved(&other);
    assert_eq!(other.balances[0].available, Dec::zero());
    assert_eq!(other.balances[1].available, d(".0775"));
}

#[test]
fn expiry_never_releases_an_active_reservation_and_clock_rollback_never_admits() {
    let t = Temp::new("1");
    t.fund();
    t.reserve("one");
    let original = t.bytes();
    let late = t.request.intent.expires_ns + 1;
    let r = replay_at(&t.path(), || Ok(late)).unwrap();
    conserved(&r);
    assert_eq!(r.balances[0].active_reserved, d(".0225"));
    assert_eq!(r.reservations[0].state, State::Active);
    assert_eq!(
        reserve_at(&t.path(), &t.input("one"), || Ok(late), Hooks::default())
            .unwrap()
            .0
            .result,
        "ALREADY_RESERVED"
    );
    assert!(matches!(
        reserve_at(&t.path(), &t.input("two"), || Ok(late), Hooks::default()),
        Err(Failure::NotEvaluable(_))
    ));
    assert_eq!(
        reserve_at(&t.path(), &t.input("two"), || Ok(NOW - 1), Hooks::default()).unwrap_err(),
        Failure::ClockRegression
    );
    assert_eq!(t.bytes(), original);
}

#[test]
fn clock_is_sampled_after_lock_wait_and_current_expiry_is_rechecked() {
    let t = Temp::new("1");
    t.fund();
    let held = disk::lock(&t.path(), false).unwrap();
    let time = Arc::new(AtomicU64::new(NOW));
    let called = Arc::new(AtomicBool::new(false));
    let gate = Arc::new(Barrier::new(2));
    let child_time = time.clone();
    let child_called = called.clone();
    let child_gate = gate.clone();
    let path = t.path();
    let bytes = t.input("waiting");
    let worker = std::thread::spawn(move || {
        child_gate.wait();
        reserve_at(
            &path,
            &bytes,
            || {
                child_called.store(true, Ordering::SeqCst);
                Ok(child_time.load(Ordering::SeqCst))
            },
            Hooks::default(),
        )
    });
    gate.wait();
    std::thread::sleep(Duration::from_millis(40));
    assert!(
        !called.load(Ordering::SeqCst),
        "must not sample an admission clock before acquiring authority"
    );
    time.store(t.request.intent.expires_ns + 1, Ordering::SeqCst);
    drop(held);
    assert!(matches!(
        worker.join().unwrap(),
        Err(Failure::NotEvaluable(_))
    ));
    assert!(called.load(Ordering::SeqCst));
    assert_eq!(t.replay().reservation_count, 0);
}

#[test]
fn atomic_failure_boundaries_preserve_both_funding_and_cash_conservation() {
    for operation in ["fund", "reserve"] {
        for point in [
            "before_create",
            "temp_created",
            "before_file_sync",
            "file_synced",
            "renamed",
            "directory_synced",
        ] {
            let t = Temp::new(".045");
            if operation == "reserve" {
                t.fund();
                t.reserve("one");
            }
            let old = t.bytes();
            let hooks = Hooks {
                fail_at: Some(point),
                observe: None,
            };
            let error = if operation == "fund" {
                fund_at(
                    &t.path(),
                    &canonical_bytes(&t.funding).unwrap(),
                    || Ok(NOW),
                    hooks,
                )
                .unwrap_err()
            } else {
                reserve_at(&t.path(), &t.input("two"), || Ok(NOW), hooks).unwrap_err()
            };
            let committed = matches!(point, "renamed" | "directory_synced");
            assert_eq!(
                error,
                Failure::Storage(if committed {
                    StoreError::DurabilityUnconfirmed
                } else {
                    StoreError::WriteFailed
                })
            );
            if !committed {
                assert_eq!(t.bytes(), old);
            }
            let before_retry = t.replay();
            conserved(&before_retry);
            if operation == "fund" {
                assert_eq!(before_retry.balances.len(), usize::from(committed));
                fund_at(
                    &t.path(),
                    &canonical_bytes(&t.funding).unwrap(),
                    || Ok(NOW),
                    Hooks::default(),
                )
                .unwrap();
                assert_eq!(t.replay().balances.len(), 1);
            } else {
                assert_eq!(
                    before_retry.reservation_count,
                    if committed { 2 } else { 1 }
                );
                let retry = t.reserve("two");
                assert_eq!(retry.reservation_count, 2);
                conserved(&retry);
                assert_eq!(retry.balances[0].available, Dec::zero());
            }
        }
    }
}

#[test]
fn corrupt_or_truncated_authority_rejects_even_with_a_complete_pending_copy() {
    let t = Temp::new("1");
    t.fund();
    t.reserve("one");
    let old = t.bytes();
    for variant in 0..5 {
        let mut e: serde_json::Value = serde_json::from_slice(&old).unwrap();
        let bad = match variant {
            0 => old[..old.len() / 2].to_vec(),
            1 => {
                e["snapshot"]["reservations"][0]["bounds"]["total"] = "0".into();
                e["sha256"] = canonical_hash(&e["snapshot"]).unwrap().into();
                canonical_bytes(&e).unwrap()
            }
            2 => {
                e["snapshot"]["fundings"][0]["funding"]["initial_balance"] = "1000".into();
                e["sha256"] = canonical_hash(&e["snapshot"]).unwrap().into();
                canonical_bytes(&e).unwrap()
            }
            3 => {
                e["snapshot"]["last_admission_ns"] = 0.into();
                e["sha256"] = canonical_hash(&e["snapshot"]).unwrap().into();
                canonical_bytes(&e).unwrap()
            }
            _ => {
                let repeated = e["snapshot"]["reservations"][0].clone();
                e["snapshot"]["reservations"]
                    .as_array_mut()
                    .unwrap()
                    .push(repeated);
                e["sha256"] = canonical_hash(&e["snapshot"]).unwrap().into();
                canonical_bytes(&e).unwrap()
            }
        };
        fs::write(t.path().join("snapshot.json"), &bad).unwrap();
        fs::write(t.path().join("pending.json"), &old).unwrap();
        assert!(replay_at(&t.path(), || Ok(NOW)).is_err());
        assert!(reserve_at(&t.path(), &t.input("two"), || Ok(NOW), Hooks::default()).is_err());
        assert_eq!(t.bytes(), bad);
        assert_eq!(fs::read(t.path().join("pending.json")).unwrap(), old);
    }
}

fn observe(point: &str) {
    if std::env::var("ZQ_RISK_PHASE").ok().as_deref() == Some(point) {
        fs::write(std::env::var_os("ZQ_RISK_MARKER").unwrap(), point).unwrap();
        loop {
            std::thread::park();
        }
    }
}
#[test]
fn process_worker() {
    let Some(path) = std::env::var_os("ZQ_RISK_PATH") else {
        return;
    };
    let path = PathBuf::from(path);
    let bytes = fs::read(std::env::var_os("ZQ_RISK_INPUT").unwrap()).unwrap();
    if std::env::var("ZQ_RISK_OPERATION").unwrap() == "partial" {
        unsafe {
            libc::signal(libc::SIGXFSZ, libc::SIG_IGN);
            let limit = libc::rlimit {
                rlim_cur: 128,
                rlim_max: 128,
            };
            assert_eq!(libc::setrlimit(libc::RLIMIT_FSIZE, &limit), 0);
        }
        assert_eq!(
            reserve_at(&path, &bytes, || Ok(NOW), Hooks::default()).unwrap_err(),
            Failure::Storage(StoreError::WriteFailed)
        );
        return;
    }
    match reserve_at(
        &path,
        &bytes,
        || Ok(NOW),
        Hooks {
            fail_at: None,
            observe: Some(observe),
        },
    ) {
        Ok((receipt, _)) => println!("RESULT:{}", receipt.result),
        Err(Failure::InsufficientFunds) => println!("RESULT:INSUFFICIENT"),
        other => panic!("unexpected child result: {other:?}"),
    }
}
fn worker(t: &Temp, id: &str, operation: &str, phase: &str) -> std::process::Child {
    let file = t.root.join(format!("{id}.json"));
    if !file.exists() {
        fs::write(&file, t.input(id)).unwrap();
    }
    Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "reservation_store::tests::process_worker",
            "--nocapture",
        ])
        .env("ZQ_RISK_PATH", t.path())
        .env("ZQ_RISK_INPUT", file)
        .env("ZQ_RISK_OPERATION", operation)
        .env("ZQ_RISK_PHASE", phase)
        .env("ZQ_RISK_MARKER", t.root.join("ready"))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap()
}
fn kill_ready(t: &Temp, child: &mut std::process::Child) {
    for _ in 0..500 {
        if t.root.join("ready").exists() {
            child.kill().unwrap();
            child.wait().unwrap();
            return;
        }
        if child.try_wait().unwrap().is_some() {
            panic!("child exited before crash checkpoint");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    child.kill().unwrap();
    child.wait().unwrap();
    panic!("checkpoint timed out");
}
#[test]
fn actual_processes_share_one_budget_and_one_idempotency_authority() {
    for same_id in [false, true] {
        let t = Temp::new(".045");
        t.fund();
        let children: Vec<_> = (0..8)
            .map(|n| {
                worker(
                    &t,
                    &format!("request-{}", if same_id { 0 } else { n }),
                    "reserve",
                    "",
                )
            })
            .collect();
        let mut granted = 0;
        let mut denied = 0;
        let mut duplicate = 0;
        for child in children {
            let out = child.wait_with_output().unwrap();
            assert!(
                out.status.success(),
                "{}",
                String::from_utf8_lossy(&out.stderr)
            );
            let text = String::from_utf8(out.stdout).unwrap();
            granted += usize::from(text.lines().any(|l| l == "RESULT:RESERVED"));
            denied += usize::from(text.lines().any(|l| l == "RESULT:INSUFFICIENT"));
            duplicate += usize::from(text.lines().any(|l| l == "RESULT:ALREADY_RESERVED"));
        }
        let replayed = t.replay();
        conserved(&replayed);
        assert_eq!(
            (granted, denied, duplicate),
            if same_id { (1, 0, 7) } else { (2, 6, 0) }
        );
        assert_eq!(replayed.reservation_count, granted);
        println!(
            "process contention same_id={same_id}: {granted} reserved, {denied} insufficient, {duplicate} duplicate; conserved"
        );
    }
}
#[test]
fn sigkill_recovery_never_separates_cash_from_active_reservations() {
    for phase in ["temp_created", "file_synced", "renamed", "directory_synced"] {
        let t = Temp::new(".045");
        t.fund();
        t.reserve("one");
        let old = t.bytes();
        let mut child = worker(&t, "two", "reserve", phase);
        kill_ready(&t, &mut child);
        let committed = matches!(phase, "renamed" | "directory_synced");
        let replayed = t.replay();
        conserved(&replayed);
        assert_eq!(replayed.reservation_count, if committed { 2 } else { 1 });
        if !committed {
            assert_eq!(t.bytes(), old);
        }
        let retry = t.reserve("two");
        conserved(&retry);
        assert_eq!(retry.reservation_count, 2);
        assert_eq!(retry.balances[0].available, Dec::zero());
        println!(
            "SIGKILL {phase}: restored {}, retry {}, available {} + active {} = initial {}",
            replayed.reservation_count,
            retry.result,
            retry.balances[0].available,
            retry.balances[0].active_reserved,
            retry.balances[0].initial
        );
    }
}
#[test]
fn actual_partial_write_leaves_old_balance_and_reservation_set_unchanged() {
    let t = Temp::new(".045");
    t.fund();
    t.reserve("one");
    let old = t.bytes();
    let out = worker(&t, "two", "partial", "").wait_with_output().unwrap();
    assert!(out.status.success());
    assert_eq!(t.bytes(), old);
    assert_eq!(
        fs::metadata(t.path().join("pending.json")).unwrap().len(),
        128
    );
    let replayed = t.replay();
    conserved(&replayed);
    assert_eq!(replayed.balances[0].available, d(".0225"));
    let retry = t.reserve("two");
    conserved(&retry);
    assert_eq!(retry.balances[0].available, Dec::zero());
}

#[test]
fn capacity_limit_does_not_prune_active_cash_holds() {
    let t = Temp::new("100");
    t.fund();
    let dir = disk::lock(&t.path(), true).unwrap();
    let (mut s, _) = load(&dir).unwrap();
    for n in 0..MAX_RESERVATIONS {
        let bytes = t.input(&format!("seed-{n}"));
        let r = core::decode_request(&bytes).unwrap();
        s = s.reserve(&r, &bytes_sha256(&bytes), NOW).unwrap().next;
    }
    persist(&t.path(), &dir, s, Hooks::default()).unwrap();
    drop(dir);
    let old = t.bytes();
    assert_eq!(
        reserve_at(
            &t.path(),
            &t.input("overflow"),
            || Ok(NOW),
            Hooks::default()
        )
        .unwrap_err(),
        Failure::Limit
    );
    assert_eq!(t.bytes(), old);
    conserved(&t.replay());
}
