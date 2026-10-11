use std::{net::TcpListener, path::Path};
fn run() -> std::io::Result<()> {
    let mut args = std::env::args().skip(1);
    let port = match args.next() {
        None => 8092,
        Some(flag) if flag == "--port" => args
            .next()
            .and_then(|s| s.parse::<u16>().ok())
            .filter(|p| *p > 0)
            .ok_or_else(|| std::io::Error::other("expected nonzero --port"))?,
        _ => return Err(std::io::Error::other("usage: options-ui [--port 8092]")),
    };
    if args.next().is_some() {
        return Err(std::io::Error::other("unexpected argument"));
    }
    let assets = options_workbench::curve_http::load_assets(Path::new(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../options-ui/dist"
    )))?;
    options_workbench::curve_http::serve(
        TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port))?,
        assets,
    )
}
fn main() {
    if let Err(e) = run() {
        eprintln!("options-ui: {e}");
        std::process::exit(1);
    }
}
