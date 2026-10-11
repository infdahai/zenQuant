use options_workbench::reservation_store::benchmark;
use std::{fs::OpenOptions, io::Write, path::PathBuf};
fn main() {
    let mut args = std::env::args_os().skip(1);
    let root = PathBuf::from(
        args.next()
            .expect("usage: reservation_baseline NEW_DIRECTORY (release profile required)"),
    );
    assert!(args.next().is_none());
    let report = benchmark::run(&root).expect("benchmark failed");
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(root.join("baseline.json"))
        .unwrap();
    file.write_all(&serde_json::to_vec_pretty(&report).unwrap())
        .unwrap();
    file.write_all(b"\n").unwrap();
    file.sync_all().unwrap();
    println!("{}", root.join("baseline.json").display());
}
