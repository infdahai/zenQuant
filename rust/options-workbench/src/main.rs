use options_workbench::{
    io,
    options_core::{contracts::*, inputs},
    publication::{self, Failure},
    report,
};
use std::path::PathBuf;
fn execute() -> Result<(), Failure> {
    let mut args = std::env::args_os().skip(1);
    let mut req = None;
    let mut out = None;
    let mut panel = None;
    while let Some(flag) = args.next() {
        if flag == "--help" {
            if req.is_some() || out.is_some() || panel.is_some() || args.next().is_some() {
                return Err(Failure::Invalid);
            }
            println!("options-workbench --request-file PATH --output-dir PATH [--panel-file PATH]");
            return Ok(());
        }
        let target = if flag == "--request-file" {
            &mut req
        } else if flag == "--output-dir" {
            &mut out
        } else if flag == "--panel-file" {
            &mut panel
        } else {
            return Err(Failure::Invalid);
        };
        if target.is_some() {
            return Err(Failure::Invalid);
        }
        *target = Some(PathBuf::from(args.next().ok_or(Failure::Invalid)?));
    }
    let req = req.ok_or(Failure::Invalid)?;
    let out = out.ok_or(Failure::Invalid)?;
    let bytes = publication::read_bounded(&req, MAX_REQUEST_BYTES)?;
    let request = inputs::decode_request(&bytes).map_err(|_| Failure::Invalid)?;
    if let Some(name) = &request.fixture_name
        && !matches!(
            name.as_str(),
            "terminal-eth-call" | "terminal-eth-straddle" | "terminal-vertical"
        )
    {
        return Err(Failure::Invalid);
    }
    let mut protected = vec![io::absolute(&req)?];
    protected.extend(io::trusted_roots()?);
    let (input, pins) = if let Some(path) = &panel {
        protected.push(io::absolute(path)?);
        let bytes = publication::read_bounded(path, inputs::MAX_PANEL_BYTES)?;
        let envelope = inputs::decode_panel(&bytes).map_err(|_| Failure::Invalid)?;
        let pins = io::verify_panel_sources(&envelope)?;
        for pin in pins.pins() {
            protected.push(PathBuf::from(&pin.path));
        }
        let input = inputs::apply_panel(request, envelope).map_err(|_| Failure::Invalid)?;
        (input, Some(pins))
    } else {
        (
            inputs::load_analysis(request).map_err(|_| Failure::Invalid)?,
            None,
        )
    };
    let out = io::validate_output(&out, &protected)?;
    let artifacts = report::render(&input, &bytes, pins).map_err(|_| Failure::Invalid)?;
    publication::publish(&out, &artifacts)
}
fn main() {
    if let Err(error) = execute() {
        eprintln!("{}", error.message());
        std::process::exit(if error == Failure::Invalid { 2 } else { 1 });
    }
}
