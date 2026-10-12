//! Isolated engineering UI server. Never reads/writes the repository paper journal.
use std::{
    net::{Ipv4Addr, TcpListener},
    path::Path,
};
fn main() -> std::io::Result<()> {
    let root = std::env::temp_dir().join(format!("zenquant-paper-browser-{}", std::process::id()));
    std::fs::create_dir(&root)?;
    println!("ENGINEERING_TEST_ONLY: {}", root.display());
    let assets = options_workbench::curve_http::load_assets(Path::new(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../options-ui/dist"
    )))?;
    options_workbench::curve_http::serve_with_paper(
        TcpListener::bind((Ipv4Addr::LOCALHOST, 8093))?,
        assets,
        options_workbench::paper_store::Store::engineering(root.join("journal")),
    )
}
