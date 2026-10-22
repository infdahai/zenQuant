//! Local files in, audit JSON to stdout. No network, account or state mutation.
use options_core::contracts::MAX_REQUEST_BYTES;
use options_workbench::{publication::read_bounded, vix_snapshot};
use std::{io::Write, path::Path};
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 3 {
        return Err("usage: vix-snapshot REQUEST.json MANIFEST.json EVIDENCE_DIR".into());
    }
    let read = |p: &Path| {
        read_bounded(p, MAX_REQUEST_BYTES)
            .map_err(|_| std::io::Error::other("missing, unsafe or oversized input file"))
    };
    let output = vix_snapshot::evaluate(
        &read(Path::new(&args[0]))?,
        &read(Path::new(&args[1]))?,
        Path::new(&args[2]),
    )?;
    let mut stdout = std::io::stdout().lock();
    stdout.write_all(&output)?;
    stdout.write_all(b"\n")?;
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("vix-snapshot: {e}");
        std::process::exit(2);
    }
}
