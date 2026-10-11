//! Opt-in measurement harness. Isolated synthetic authorities are private to this
//! harness; production CLI has no authority-directory or evaluation-clock override.
use super::*;
use options_core::{decimal::Dec, fixtures::mathematical_reservation_case, reservations::Request};
use serde_json::{Value, json};
use std::{
    fs,
    sync::{Arc, Barrier},
};

const SAMPLES: usize = 32;
const WARMUP: usize = 4;
#[derive(Serialize)]
struct Sample {
    sequence: u64,
    end_to_end_ns: u64,
    lock_acquisition_ns: u64,
    receipt_bytes: usize,
}
fn ns(d: Duration) -> u64 {
    u64::try_from(d.as_nanos()).unwrap()
}
fn summary(values: impl Iterator<Item = u64>) -> Value {
    let mut xs: Vec<_> = values.collect();
    xs.sort_unstable();
    let percentile = |p: usize| xs[(xs.len() * p).div_ceil(100) - 1];
    json!({"samples":xs.len(), "p50_ns":percentile(50), "p95_ns":percentile(95), "p99_ns":percentile(99),
        "min_ns":xs[0], "max_ns":xs[xs.len()-1], "mean_ns_rounded_down":(xs.iter().map(|n| *n as u128).sum::<u128>() / xs.len() as u128) as u64})
}
fn bytes_for(template: &Request, id: &str) -> Result<Vec<u8>> {
    let mut r = template.clone();
    r.intent.request_id = id.into();
    r.intent_sha256 = canonical_hash(&r.intent).map_err(|_| Failure::InvalidInput)?;
    canonical_bytes(&r).map_err(|_| Failure::InvalidInput)
}
fn one_case(root: &Path, count: usize, concurrency: usize) -> Result<Value> {
    let dir = root.join(format!("records-{count}-workers-{concurrency}"));
    fs::create_dir(&dir).map_err(|_| Failure::InvalidInput)?;
    let authority = dir.join("authority");
    let now = wall_clock_ns()?;
    let (funding, template) = mathematical_reservation_case(now, Dec::parse("1000").unwrap())
        .map_err(|_| Failure::InvalidInput)?;
    initialize_at(&authority, wall_clock_ns)?;
    fund_at(
        &authority,
        &canonical_bytes(&funding).unwrap(),
        wall_clock_ns,
        Hooks::default(),
    )?;
    // Setup is excluded from samples. Seed a fully validated equivalent historical
    // state in one commit rather than measuring quadratic setup as request latency.
    let held = disk::lock(&authority, true)?;
    let (mut s, _) = load(&held)?;
    let admitted = wall_clock_ns()?;
    for n in 0..count - WARMUP {
        let bytes = bytes_for(&template, &format!("seed-{n}"))?;
        let request = core::decode_request(&bytes).map_err(|_| Failure::InvalidInput)?;
        s = s.reserve(&request, &bytes_sha256(&bytes), admitted)?.next;
    }
    persist(&authority, &held, s, Hooks::default())?;
    drop(held);
    for n in 0..WARMUP {
        reserve_at(
            &authority,
            &bytes_for(&template, &format!("warmup-{n}"))?,
            wall_clock_ns,
            Hooks::default(),
        )?;
    }
    let bytes_before = fs::metadata(authority.join("snapshot.json"))
        .map_err(|_| Failure::Corrupt)?
        .len();
    let mut files = Vec::new();
    for n in 0..SAMPLES {
        let path = dir.join(format!("request-{n}.json"));
        fs::write(&path, bytes_for(&template, &format!("measured-{n}"))?)
            .map_err(|_| Failure::InvalidInput)?;
        files.push(path);
    }
    let files = Arc::new(files);
    let gate = Arc::new(Barrier::new(concurrency + 1));
    let mut workers = Vec::new();
    for worker in 0..concurrency {
        let files = files.clone();
        let gate = gate.clone();
        let authority = authority.clone();
        workers.push(std::thread::spawn(move || -> Result<Vec<Sample>> {
            let mut samples = Vec::new();
            gate.wait();
            for index in (worker..SAMPLES).step_by(concurrency) {
                let started = Instant::now();
                let bytes = request_bytes(&files[index], &authority)?;
                let (receipt, wait) =
                    reserve_at(&authority, &bytes, wall_clock_ns, Hooks::default())?;
                if receipt.result != "RESERVED" {
                    return Err(Failure::Corrupt);
                }
                let encoded = canonical_bytes(&receipt).map_err(|_| Failure::Corrupt)?;
                let receipt_bytes = encoded.len();
                std::hint::black_box(encoded);
                samples.push(Sample {
                    sequence: receipt.reservations[0].sequence,
                    end_to_end_ns: ns(started.elapsed()),
                    lock_acquisition_ns: ns(wait),
                    receipt_bytes,
                });
            }
            Ok(samples)
        }));
    }
    let case_start = Instant::now();
    gate.wait();
    let mut samples = Vec::new();
    for worker in workers {
        samples.extend(worker.join().map_err(|_| Failure::Corrupt)??);
    }
    let elapsed = ns(case_start.elapsed());
    samples.sort_by_key(|s| s.sequence);
    let final_state = replay_at(&authority, wall_clock_ns)?;
    if final_state.reservation_count != count + SAMPLES {
        return Err(Failure::Corrupt);
    }
    let bytes_after = fs::metadata(authority.join("snapshot.json"))
        .map_err(|_| Failure::Corrupt)?
        .len();
    eprintln!(
        "measured records {count}..{}, workers {concurrency}, samples {SAMPLES}",
        count + SAMPLES
    );
    Ok(
        json!({"initial_records":count,"final_records":count+SAMPLES,"capacity":MAX_RESERVATIONS,"workers":concurrency,
        "warmup_operations":WARMUP,"snapshot_bytes_before":bytes_before,"snapshot_bytes_after":bytes_after,
        "case_elapsed_ns":elapsed,"latency":summary(samples.iter().map(|s| s.end_to_end_ns)),
        "lock_acquisition":summary(samples.iter().map(|s| s.lock_acquisition_ns)),"samples":samples,
        "final_balance":final_state.balances,"final_snapshot_sha256":final_state.snapshot_sha256}),
    )
}
/// Runs real decode/locking/checking/serialization/fsync operations, not a mock IO path.
/// Includes input file read through receipt JSON encoding; excludes process startup/stdout transport.
pub fn run(root: &Path) -> Result<Value> {
    if cfg!(debug_assertions) {
        return Err(Failure::InvalidInput);
    }
    let root = disk::safe_path(root)?;
    fs::create_dir(&root).map_err(|_| Failure::InvalidInput)?;
    let started = wall_clock_ns()?.to_string();
    let mut cases = Vec::new();
    for count in [8, 512, 960] {
        for workers in [1, 2] {
            cases.push(one_case(&root, count, workers)?);
        }
    }
    let read = |path| {
        fs::read_to_string(path)
            .unwrap_or_else(|_| "unavailable".into())
            .trim()
            .to_owned()
    };
    let cpu = read("/proc/cpuinfo")
        .lines()
        .find_map(|l| {
            l.strip_prefix("model name")
                .and_then(|v| v.split_once(':').map(|(_, value)| value.trim().to_owned()))
        })
        .unwrap_or_else(|| "unavailable".into());
    Ok(
        json!({"schema_version":"simulated-reservation-benchmark.rust.v1","started_at_unix_ns":started,"finished_at_unix_ns":wall_clock_ns()?.to_string(),
        "profile":"release","os":std::env::consts::OS,"arch":std::env::consts::ARCH,"cpu_model":cpu,
        "available_parallelism":std::thread::available_parallelism().map(|n| n.get()).unwrap_or(0),
        "cpu_max":read("/sys/fs/cgroup/cpu.max"),"memory_max":read("/sys/fs/cgroup/memory.max"),"kernel":read("/proc/sys/kernel/osrelease"),
        "scope":"warm-cache synthetic single-owner single-option workload; full snapshot validation/write; blocking authority lock; all normal file and directory fsyncs; no execution",
        "timing_boundary":"request file read to complete receipt JSON encoding; includes lock acquisition/wait and fsync; excludes process startup, stdout and fixture setup",
        "quantiles":"nearest-rank; 32 samples per case, p99 is maximum; no SLA or peak-throughput claim",
        "cases":cases}),
    )
}
