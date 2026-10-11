//! Generate explicitly mathematical, currently timed inputs; never contacts an account.
use options_workbench::{
    options_core::{
        canonical::canonical_bytes, decimal::Dec, fixtures::mathematical_reservation_case,
    },
    reservation_store::wall_clock_ns,
};
use std::{fs, io::Write, path::PathBuf};
fn main() {
    let mut args = std::env::args_os().skip(1);
    let output = PathBuf::from(
        args.next()
            .expect("usage: reservation_fixture NEW_DIRECTORY"),
    );
    assert!(args.next().is_none());
    let (funding, request) =
        mathematical_reservation_case(wall_clock_ns().unwrap(), Dec::parse("1").unwrap()).unwrap();
    fs::create_dir(&output).unwrap();
    for (name, bytes) in [
        ("funding.json", canonical_bytes(&funding).unwrap()),
        ("request.json", canonical_bytes(&request).unwrap()),
    ] {
        let mut file = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(output.join(name))
            .unwrap();
        file.write_all(&bytes).unwrap();
        file.sync_all().unwrap();
    }
}
