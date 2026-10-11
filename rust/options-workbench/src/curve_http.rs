//! Loopback-only, bounded HTTP adapter; no trading state, arbitrary file API or job scheduler.
use std::{
    collections::BTreeMap,
    fs,
    io::{self, Read, Write},
    net::{TcpListener, TcpStream},
    path::Path,
    sync::Arc,
    thread,
    time::{Duration, Instant},
};
const BODY_LIMIT: usize = options_core::contracts::MAX_REQUEST_BYTES;
const HEAD_LIMIT: usize = 8192;
const DEADLINE: Duration = Duration::from_secs(3);
type Assets = BTreeMap<String, (String, Vec<u8>)>;
fn invalid() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, "invalid HTTP request")
}

/// Only the explicitly built HTML/CSS/JS bundle is read, once at startup.
pub fn load_assets(root: &Path) -> io::Result<Assets> {
    fn visit(root: &Path, dir: &Path, out: &mut Assets, total: &mut usize) -> io::Result<()> {
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let kind = entry.file_type()?;
            let path = entry.path();
            if kind.is_symlink() {
                return Err(invalid());
            }
            if kind.is_dir() {
                visit(root, &path, out, total)?;
                continue;
            }
            let mime = match path.extension().and_then(|s| s.to_str()) {
                Some("html") => "text/html; charset=utf-8",
                Some("js") => "text/javascript; charset=utf-8",
                Some("css") => "text/css; charset=utf-8",
                _ => continue,
            };
            let size = usize::try_from(entry.metadata()?.len()).map_err(|_| invalid())?;
            *total = total.checked_add(size).ok_or_else(invalid)?;
            if *total > 16 * 1024 * 1024 || out.len() >= 128 {
                return Err(invalid());
            }
            out.insert(
                format!(
                    "/{}",
                    path.strip_prefix(root)
                        .map_err(|_| invalid())?
                        .to_str()
                        .ok_or_else(invalid)?
                ),
                (mime.into(), fs::read(&path)?),
            );
        }
        Ok(())
    }
    let mut out = BTreeMap::new();
    visit(root, root, &mut out, &mut 0)?;
    let index = out.get("/index.html").ok_or_else(invalid)?.clone();
    out.insert("/".into(), index);
    Ok(out)
}

fn response(s: &mut TcpStream, status: &str, mime: &str, body: &[u8]) -> io::Result<()> {
    write!(
        s,
        "HTTP/1.1 {status}\r\nContent-Type: {mime}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'\r\n\r\n",
        body.len()
    )?;
    s.write_all(body)
}
struct Request {
    path: String,
    method: String,
    body: Vec<u8>,
}
fn read_request(s: &mut TcpStream, host: &str) -> io::Result<Request> {
    let started = Instant::now();
    let mut bytes = Vec::new();
    let mut chunk = [0; 4096];
    let (head_len, path, method, length) = loop {
        if started.elapsed() >= DEADLINE {
            return Err(invalid());
        }
        s.set_read_timeout(Some(
            DEADLINE
                .saturating_sub(started.elapsed())
                .max(Duration::from_millis(1)),
        ))?;
        let n = s.read(&mut chunk)?;
        if n == 0 {
            return Err(invalid());
        }
        bytes.extend_from_slice(&chunk[..n]);
        let mut headers = [httparse::EMPTY_HEADER; 32];
        let mut req = httparse::Request::new(&mut headers);
        match req.parse(&bytes).map_err(|_| invalid())? {
            httparse::Status::Partial => {
                if bytes.len() > HEAD_LIMIT {
                    return Err(invalid());
                }
            }
            httparse::Status::Complete(head_len) => {
                if head_len > HEAD_LIMIT || req.version != Some(1) {
                    return Err(invalid());
                }
                let mut seen = BTreeMap::new();
                for h in req.headers.iter() {
                    if seen.insert(h.name.to_ascii_lowercase(), h.value).is_some() {
                        return Err(invalid());
                    }
                }
                let value = |k: &str| seen.get(k).copied();
                if value("host") != Some(host.as_bytes())
                    || value("transfer-encoding").is_some()
                    || value("expect").is_some()
                {
                    return Err(invalid());
                }
                if value("sec-fetch-site") == Some(b"cross-site") {
                    return Err(invalid());
                }
                let method = req.method.ok_or_else(invalid)?.to_string();
                let length = match value("content-length") {
                    None => 0,
                    Some(v) => std::str::from_utf8(v)
                        .map_err(|_| invalid())?
                        .parse::<usize>()
                        .map_err(|_| invalid())?,
                };
                if length > BODY_LIMIT {
                    return Err(invalid());
                }
                if method == "POST" {
                    let origin = format!("http://{host}");
                    if value("origin") != Some(origin.as_bytes())
                        || value("x-zenquant-request") != Some(b"1")
                        || value("content-type") != Some(b"application/json")
                        || length == 0
                    {
                        return Err(invalid());
                    }
                } else if method != "GET" || length != 0 {
                    return Err(invalid());
                }
                break (
                    head_len,
                    req.path.ok_or_else(invalid)?.to_string(),
                    method,
                    length,
                );
            }
        }
    };
    while bytes.len() < head_len + length {
        if started.elapsed() >= DEADLINE {
            return Err(invalid());
        }
        s.set_read_timeout(Some(
            DEADLINE
                .saturating_sub(started.elapsed())
                .max(Duration::from_millis(1)),
        ))?;
        let remain = (head_len + length - bytes.len()).min(chunk.len());
        let n = s.read(&mut chunk[..remain])?;
        if n == 0 {
            return Err(invalid());
        }
        bytes.extend_from_slice(&chunk[..n]);
    }
    if bytes.len() != head_len + length {
        return Err(invalid());
    }
    Ok(Request {
        path,
        method,
        body: bytes[head_len..].to_vec(),
    })
}

pub fn handle(mut s: TcpStream, host: &str, assets: &Assets) -> io::Result<()> {
    s.set_write_timeout(Some(DEADLINE))?;
    let req = match read_request(&mut s, host) {
        Ok(r) => r,
        Err(_) => {
            return response(
                &mut s,
                "400 Bad Request",
                "application/json",
                b"{\"error\":\"INVALID_HTTP_REQUEST\"}",
            );
        }
    };
    if req.method == "GET" {
        return match assets.get(&req.path) {
            Some((mime, b)) => response(&mut s, "200 OK", mime, b),
            None => response(
                &mut s,
                "404 Not Found",
                "application/json",
                b"{\"error\":\"NOT_FOUND\"}",
            ),
        };
    }
    if !matches!(req.path.as_str(), "/api/curve" | "/api/synthetic") {
        return response(
            &mut s,
            "404 Not Found",
            "application/json",
            b"{\"error\":\"NOT_FOUND\"}",
        );
    }
    match crate::curve_api::evaluate(&req.path, &req.body) {
        Ok(body) => response(&mut s, "200 OK", "application/json", &body),
        Err(e) => response(
            &mut s,
            "422 Unprocessable Content",
            "application/json",
            &serde_json::to_vec(&serde_json::json!({"error":e.code})).map_err(|_| invalid())?,
        ),
    }
}

pub fn serve(listener: TcpListener, assets: Assets) -> io::Result<()> {
    let addr = listener.local_addr()?;
    if !addr.ip().is_loopback() {
        return Err(invalid());
    }
    let host = addr.to_string();
    let assets = Arc::new(assets);
    println!(
        "ZenQuant synthetic/conditional options UI: http://{host}/ (development loopback only)"
    );
    // Two bounded workers keep a cancelled/slow connection from monopolizing the UI.
    // No detached thread per request or unbounded application queue.
    let workers: Vec<_> = (0..2)
        .map(|_| {
            let listener = listener.try_clone()?;
            let assets = Arc::clone(&assets);
            let host = host.clone();
            Ok(thread::spawn(move || -> io::Result<()> {
                for s in listener.incoming() {
                    let _ = handle(s?, &host, &assets);
                }
                Ok(())
            }))
        })
        .collect::<io::Result<_>>()?;
    for w in workers {
        w.join().map_err(|_| invalid())??;
    }
    Ok(())
}
