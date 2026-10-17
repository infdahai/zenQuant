use std::{
    env, fs,
    path::{Path, PathBuf},
};
fn gather(p: &Path, v: &mut Vec<PathBuf>) {
    for e in fs::read_dir(p).unwrap() {
        let p = e.unwrap().path();
        if p.is_dir() {
            gather(&p, v)
        } else if p.extension().is_some_and(|e| e == "rs") {
            v.push(p)
        }
    }
}
fn main() {
    let base = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let root = base.parent().unwrap();
    let mut paths = vec![
        base.join("build.rs"),
        base.join("Cargo.toml"),
        base.join("fixtures/vix/demo.json"),
        root.join("Cargo.toml"),
        root.join("options-core/Cargo.toml"),
    ];
    gather(&base.join("src"), &mut paths);
    gather(&root.join("options-core/src"), &mut paths);
    paths.sort();
    let mut out = String::from("pub const CODE_FILES: &[(&str, &[u8])] = &[\n");
    for p in paths {
        println!("cargo:rerun-if-changed={}", p.display());
        out.push_str(&format!(
            "({:?}, include_bytes!({:?})),\n",
            p.strip_prefix(root).unwrap().to_str().unwrap(),
            p.to_str().unwrap()
        ));
    }
    out.push_str("];\n");
    out.push_str(&format!(
        "pub const LOCK_BYTES: &[u8] = include_bytes!({:?});\n",
        root.join("Cargo.lock").to_str().unwrap()
    ));
    println!(
        "cargo:rerun-if-changed={}",
        root.join("Cargo.lock").display()
    );
    fs::write(
        PathBuf::from(env::var("OUT_DIR").unwrap()).join("identities.rs"),
        out,
    )
    .unwrap();
}
