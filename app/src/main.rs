//! Orbit app shell (build step 8, README §3): a native window (WKWebView) and app menu around an orbit server's page.
//!
//! - The app is only the face: it shows whichever orbit server it is pointed at (`$ORBIT_SERVER`, else the
//!   `server` of <state>/app.json, else this Mac's on 127.0.0.1). This Mac's server is started when nothing listens
//!   (`python -m orbit`); a server elsewhere (a Linux box over Tailscale) is only connected to, with its token.
//!   The server outlives the app; closing the window only hides it; the Dock icon brings it back.
//! - The page talks to the shell by navigating a hidden frame to http://orbit-shell.invalid/<what>?… (cancelled here):
//!   `connect?url=` switches servers, `open?url=` opens a link on this Mac (a remote server would open it on its own).
//! - Owns the keys the page may never see: ⌘⇧R reloads the UI even when the page is broken or not loaded yet;
//!   ⌘` and ⌘, are routed to the page (`deskKey`), like the menu items. Native shortcuts wait while a dialog is up
//!   (`deskModal`).
//! - The Edit menu gives WebKit its ⌘C / ⌘V / ⌘X / ⌘A / ⌘Z. No Close item: ⌘W belongs to the page.
//! - Shows the page's unread count on the Dock icon and remembers the window's frame.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use std::io::Write;

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

/// The repository the app was built from: holds `orbit/`, `web/` and `.venv/`. `$ORBIT_ROOT` overrides it.
const BUILD_ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/..");

/// Window button position for the default 38pt title bar. The web layout reserves at least 32pt at compact
/// interface sizes (web/styles/layout.css, --bar). Measured: a button's centre sits 2.5pt above this (12.5 put it at
/// 10pt), so 21.5 centres it at 19pt, the bar's middle.
const TRAFFIC_X: f64 = 16.0;
const TRAFFIC_Y: f64 = 21.5;

/// The server process this app started (None when it reused a running one).
struct Server(Mutex<Option<Child>>);

fn root() -> PathBuf {
    let p = std::env::var("ORBIT_ROOT").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from(BUILD_ROOT));
    p.canonicalize().unwrap_or(p)
}

/// A variant's own setting (build-app.sh: ORBIT_APP_PORT, ORBIT_APP_HOME, ORBIT_APP_STATE_DIR, from its Info.plist)
/// wins over the general one (ORBIT_PORT …), which a terminal inside another Orbit may have exported.
fn app_env(own: &str, general: &str) -> Option<String> {
    std::env::var(own).or_else(|_| std::env::var(general)).ok().filter(|v| !v.trim().is_empty())
}

fn port() -> u16 {
    app_env("ORBIT_APP_PORT", "ORBIT_PORT").and_then(|v| v.parse().ok()).unwrap_or(8770)
}

/// The server the window shows: where it is, its token (none for this Mac's), and whether it is this Mac's.
#[derive(Clone)]
struct Target {
    host: String,
    port: u16,
    token: String,
    local: bool,
}

impl Target {
    fn base(&self) -> String {
        let h = if self.host.contains(':') { format!("[{}]", self.host) } else { self.host.clone() };
        format!("http://{h}:{}", self.port)
    }
    fn label(&self) -> String {
        if self.local { format!("this Mac (127.0.0.1:{})", self.port) } else { format!("{}:{}", self.host, self.port) }
    }
    fn addr(&self) -> Option<SocketAddr> {
        use std::net::ToSocketAddrs;
        (self.host.as_str(), self.port).to_socket_addrs().ok()?.next()
    }
}

fn app_json() -> PathBuf {
    state_dir().join("app.json")
}

/// A URL from `orbit url` (http://host:port/?token=T), or "" for this Mac's server.
fn parse_target(raw: &str) -> Result<Target, String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Ok(Target { host: "127.0.0.1".into(), port: port(), token: String::new(), local: true });
    }
    let u: Url = raw.parse().map_err(|_| format!("not a URL: {raw}"))?;
    if u.scheme() != "http" && u.scheme() != "https" {
        return Err(format!("not an http URL: {raw}"));
    }
    let host = u.host_str().ok_or_else(|| format!("no host in {raw}"))?.trim_matches(['[', ']']).to_string();
    let token = u.query_pairs().find(|(k, _)| k == "token").map(|(_, v)| v.to_string()).unwrap_or_default();
    let local = host == "127.0.0.1" || host == "localhost" || host == "::1";
    if !local && token.is_empty() {
        return Err("the URL needs its ?token= (on that machine: orbit url)".into());
    }
    Ok(Target { host, port: u.port_or_known_default().unwrap_or(8770), token, local })
}

fn saved_server() -> String {
    if let Ok(v) = std::env::var("ORBIT_SERVER") {
        return v;
    }
    let text = fs::read_to_string(app_json()).unwrap_or_default();
    serde_json::from_str::<serde_json::Value>(&text)
        .ok()
        .and_then(|v| v.get("server").and_then(|s| s.as_str()).map(String::from))
        .unwrap_or_default()
}

fn save_server(raw: &str) {
    let mut v = serde_json::from_str::<serde_json::Value>(&fs::read_to_string(app_json()).unwrap_or_default())
        .unwrap_or_else(|_| serde_json::json!({}));
    v["server"] = serde_json::Value::String(raw.trim().to_string());
    let _ = fs::create_dir_all(state_dir());
    let path = app_json();
    let _ = fs::write(&path, serde_json::to_string_pretty(&v).unwrap_or_default());
    use std::os::unix::fs::PermissionsExt;
    let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));  // it holds a token
}

static TARGET: Mutex<Option<Target>> = Mutex::new(None);

fn target() -> Target {
    let mut t = TARGET.lock().unwrap();
    if t.is_none() {
        *t = Some(parse_target(&saved_server()).unwrap_or_else(|_| parse_target("").unwrap()));
    }
    t.clone().unwrap()
}

fn set_target(t: Target) {
    *TARGET.lock().unwrap() = Some(t);
}

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
}

/// The server's state folder: $ORBIT_STATE_DIR, else $ORBIT_HOME, else ~/.orbit (as the server picks it). The folder's
/// old name is moved only when asked (`orbit migrate-home`): a variant (another $ORBIT_HOME) must not touch it.
fn state_dir() -> PathBuf {
    app_env("ORBIT_APP_STATE_DIR", "ORBIT_STATE_DIR")
        .or_else(|| app_env("ORBIT_APP_HOME", "ORBIT_HOME"))
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".orbit"))
}

/// The app's name: $ORBIT_APP_NAME (a variant built with `build-app.sh --name`), else Orbit.
fn app_name() -> String {
    std::env::var("ORBIT_APP_NAME").ok().filter(|v| !v.trim().is_empty()).unwrap_or_else(|| "Orbit".into())
}

fn log_path() -> PathBuf {
    state_dir().join("server.log")
}

/// The page's URL; for a server elsewhere the first load carries the token (it becomes a cookie, then leaves the URL).
fn orbit_url(first: bool) -> Url {
    let t = target();
    let q = if !t.local && first { format!("?token={}", t.token) } else { String::new() };
    format!("{}/{q}", t.base()).parse().expect("valid url")
}

/// A window's page: the first window ("main") keeps the plain address (its state keys as before); another carries its
/// label (?win=: what it looks at is its own, core.js) and, when it opens, the group it starts on.
fn window_url(label: &str, first: bool, group: &str) -> Url {
    let mut u = orbit_url(first);
    if label != "main" || !group.is_empty() {
        let mut q = u.query_pairs_mut();
        if label != "main" {
            q.append_pair("win", label);
        }
        if !group.is_empty() {
            q.append_pair("group", group);
        }
    }
    u
}

/// The window the user works in: the focused one, else the first.
fn front(app: &AppHandle) -> Option<WebviewWindow> {
    let ws = app.webview_windows();
    ws.values().find(|w| w.is_focused().unwrap_or(false)).cloned().or_else(|| ws.get("main").cloned())
}

/// This Mac's port (the server the app may start and stop).
fn port_open() -> bool {
    TcpStream::connect_timeout(&SocketAddr::from(([127, 0, 0, 1], port())), Duration::from_millis(200)).is_ok()
}

/// Whether the server the window shows answers.
fn target_open() -> bool {
    let t = target();
    if t.local {
        return port_open();
    }
    t.addr().map(|a| TcpStream::connect_timeout(&a, Duration::from_millis(1500)).is_ok()).unwrap_or(false)
}

/// Settings the server reads from the environment; an app opened from Finder only sees them via the login shell.
const PASS_ENV: [&str; 5] = ["ORBIT_DATA_ROOT", "ORBIT_VAULT", "ORBIT_STATE_DIR", "ORBIT_HOME", "ORBIT_EDITOR"];

/// The login shell's environment (PATH and Orbit's settings): launchd gives an app a bare one.
fn login_env() -> Vec<(String, String)> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into());
    let out = Command::new(&shell)
        .args(["-l", "-c", "env"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
        .unwrap_or_default();
    out.lines()
        .filter_map(|l| l.split_once('='))
        .filter(|(k, _)| *k == "PATH" || PASS_ENV.contains(k))
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

/// An app opened from Finder gets launchd's bare PATH; agents need the user's (claude, uv, node, git…).
fn login_path(env: &[(String, String)]) -> String {
    let from_shell = env.iter().find(|(k, _)| k == "PATH").map(|(_, v)| v.clone());
    let h = home();
    let mut parts: Vec<String> = from_shell.unwrap_or_default().split(':').filter(|s| !s.is_empty()).map(String::from).collect();
    for extra in [
        h.join(".local/bin").display().to_string(),
        h.join(".cargo/bin").display().to_string(),
        "/opt/homebrew/bin".into(),
        "/usr/local/bin".into(),
        "/usr/bin".into(),
        "/bin".into(),
        "/usr/sbin".into(),
        "/sbin".into(),
    ] {
        if !parts.contains(&extra) {
            parts.push(extra);
        }
    }
    parts.join(":")
}

fn spawn_server() -> Result<Child, String> {
    let root = root();
    let venv_python = root.join(".venv/bin/python");
    let (exe, mut args): (String, Vec<String>) = if venv_python.exists() {
        (venv_python.display().to_string(), vec![])
    } else {
        ("uv".into(), vec!["run".into(), "python".into()])
    };
    args.extend(["-m", "orbit", "--port"].iter().map(|s| s.to_string()));
    args.push(port().to_string());

    fs::create_dir_all(state_dir()).map_err(|e| format!("cannot create {}: {e}", state_dir().display()))?;
    if fs::metadata(log_path()).map(|m| m.len() > 5_000_000).unwrap_or(false) {
        let _ = fs::rename(log_path(), log_path().with_extension("log.1"));  // keep one old log
    }
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path())
        .map_err(|e| format!("cannot open {}: {e}", log_path().display()))?;
    let log2 = log.try_clone().map_err(|e| e.to_string())?;
    let env = login_env();
    let mut cmd = Command::new(&exe);
    for (k, v) in env.iter().filter(|(k, _)| k != "PATH") {
        if std::env::var_os(k).is_none() {
            cmd.env(k, v);
        }
    }
    cmd.args(&args)
        .current_dir(&root)
        .env("PATH", login_path(&env))
        .env("PYTHONUNBUFFERED", "1")
        .envs(["ORBIT_HOME", "ORBIT_STATE_DIR"].iter().filter_map(|k| {  // a variant's folders, for its server
            std::env::var(k.replace("ORBIT_", "ORBIT_APP_")).ok().map(|v| (k.to_string(), v))
        }))
        .stdin(Stdio::null())
        .stdout(log)
        .stderr(log2)
        // Its own process group: the server (and the agents it runs) outlive the app. ⌘Q closes the window
        // app only; "Quit Orbit and Stop Agents" (⌥⌘Q) stops them too.
        .process_group(0)
        .spawn()
        .map_err(|e| format!("cannot start {exe} in {}: {e}", root.display()))
}

static STOP_ON_EXIT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// The server's pid, from the file it writes at start (a server this app instance did not start).
fn server_pid() -> Option<i32> {
    // server.pid; dispatcher.pid is what a server from before the rename wrote
    ["server.pid", concat!("dis", "patcher.pid")]
        .iter()
        .find_map(|f| fs::read_to_string(state_dir().join(f)).ok()?.trim().parse().ok())
}

/// Stop whichever server serves the port: ours (a Child) or one an earlier launch left running.
fn stop_any_server(app: &AppHandle) {
    if let Some(mut c) = app.state::<Server>().0.lock().unwrap().take() {
        return stop_server(&mut c);
    }
    if let Some(pid) = server_pid() {
        unsafe {
            libc::kill(pid, libc::SIGTERM);
        }
        let end = Instant::now() + Duration::from_secs(6);
        while port_open() && Instant::now() < end {
            thread::sleep(Duration::from_millis(100));
        }
        if port_open() {
            unsafe {
                libc::kill(pid, libc::SIGKILL);
            }
        }
    }
}

/// "Quit Orbit and Stop Agents": this Mac's server closes every agent and ends the Harness Server
/// (POST /api/shutdown {"agents": "stop"}; a SIGTERM would hand the agents off to the next server instead).
/// Waits for it to exit; false when it did not take the request (then SIGTERM, as before).
fn stop_with_agents(app: &AppHandle) -> bool {
    let Ok(mut s) = TcpStream::connect_timeout(&SocketAddr::from(([127, 0, 0, 1], port())), Duration::from_millis(800)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
    let body = r#"{"agents":"stop"}"#;
    let req = format!(
        "POST /api/shutdown HTTP/1.0\r\nHost: 127.0.0.1:{}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
        port(),
        body.len()
    );
    let mut out = String::new();
    if s.write_all(req.as_bytes()).is_err() || s.read_to_string(&mut out).is_err() && out.is_empty() {
        return false;
    }
    if out.split_whitespace().nth(1) != Some("200") {
        return false;
    }
    // Closing the agents takes a few seconds each at most; the server exits after the last one.
    let end = Instant::now() + Duration::from_secs(30);
    while Instant::now() < end {
        let gone = match app.state::<Server>().0.lock().unwrap().as_mut() {
            Some(c) => !matches!(c.try_wait(), Ok(None)),
            None => !port_open(),
        };
        if gone {
            return true;
        }
        thread::sleep(Duration::from_millis(100));
    }
    false
}

/// SIGTERM lets the server close its agent processes; SIGKILL only if it hangs.
fn stop_server(child: &mut Child) {
    unsafe {
        libc::kill(child.id() as libc::pid_t, libc::SIGTERM);
    }
    let end = Instant::now() + Duration::from_secs(6);
    while Instant::now() < end {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn log_tail() -> String {
    let mut f = match fs::File::open(log_path()) {
        Ok(f) => f,
        Err(_) => return String::new(),
    };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = f.seek(SeekFrom::Start(len.saturating_sub(1500)));
    let mut s = String::new();
    let _ = f.read_to_string(&mut s);
    s.lines().rev().take(8).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join("\n")
}

fn js_string(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => {}
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn show_failure(app: &AppHandle, msg: &str) {
    let ws: Vec<WebviewWindow> = app.webview_windows().into_values().collect();
    for w in &ws {
        let _ = w.navigate("tauri://localhost/index.html".parse().expect("url"));
    }
    thread::sleep(Duration::from_millis(400));
    for w in &ws {
        let _ = w.eval(&format!("window.orbitFailed && orbitFailed({})", js_string(msg)));
    }
}

/// Every window to its page (the server is up).
fn open_pages(app: &AppHandle) {
    for (label, w) in app.webview_windows() {
        let _ = w.navigate(window_url(&label, true, ""));
    }
}

/// Start (or reuse) the server, wait for it, then load the page. Runs off the main thread.
fn boot(app: AppHandle) {
    thread::spawn(move || {
        let t = target();
        if !t.local {
            // A server on another machine: never started from here, only reached.
            if !target_open() {
                let msg = format!("Cannot reach the orbit server at {}.\nOn that machine: orbit serve --remote (in tmux), then orbit url.", t.label());
                return show_failure(&app, &msg);
            }
            return open_pages(&app);
        }
        if !port_open() {
            match spawn_server() {
                Ok(child) => *app.state::<Server>().0.lock().unwrap() = Some(child),
                Err(e) => return show_failure(&app, &e),
            }
        }
        let end = Instant::now() + Duration::from_secs(30);
        while !port_open() {
            let exited = app
                .state::<Server>()
                .0
                .lock()
                .unwrap()
                .as_mut()
                .and_then(|c| c.try_wait().ok().flatten());
            if let Some(status) = exited {
                *app.state::<Server>().0.lock().unwrap() = None;
                let msg = format!("The server exited ({status}).\n{}\n\nLog: {}", log_tail(), log_path().display());
                return show_failure(&app, &msg);
            }
            if Instant::now() > end {
                let msg = format!("The server did not open port {} in 30 s.\n\nLog: {}", port(), log_path().display());
                return show_failure(&app, &msg);
            }
            thread::sleep(Duration::from_millis(100));
        }
        open_pages(&app);
    });
}

/// The page (or the failure page) asked to show another server: "" is this Mac's.
fn connect(app: &AppHandle, raw: &str) {
    match parse_target(raw) {
        Ok(t) => {
            save_server(if t.local { "" } else { raw });
            set_target(t);
            boot(app.clone());
        }
        Err(e) => show_failure(app, &e),
    }
}

/// Navigations to http://orbit-shell.invalid/… are the page's requests to the shell; everything else proceeds.
fn shell_request(app: &AppHandle, url: &Url) -> bool {
    if url.host_str() != Some("orbit-shell.invalid") {
        return true;
    }
    let arg = url.query_pairs().find(|(k, _)| k == "url").map(|(_, v)| v.to_string()).unwrap_or_default();
    match url.path() {
        "/connect" => {
            let app = app.clone();
            thread::spawn(move || connect(&app, &arg));
        }
        "/new-window" => {
            let app = app.clone();
            thread::spawn(move || new_window(&app, &arg));
        }
        "/open" if arg.starts_with("http://") || arg.starts_with("https://") || arg.starts_with("mailto:") => {
            let _ = Command::new("open").arg(&arg).spawn();
        }
        _ => {}
    }
    false
}

/// ⌘⇧R: reload the page, keeping drafts (the page's own reloadUi saves them first).
fn reload(app: &AppHandle) {
    let Some(w) = front(app) else { return };
    let label = w.label().to_string();
    if !target_open() {
        return boot(app.clone());
    }
    let t = target();
    let on_orbit = w.url().map(|u| u.host_str().map(|h| h.trim_matches(['[', ']'])) == Some(t.host.as_str())).unwrap_or(false);
    if on_orbit {
        // A dialog owns the keys (its drafts would be lost); a broken page has no deskModal and reloads.
        let _ = w.eval("if (!(window.deskModal && deskModal())) (window.reloadUi || (() => location.reload()))()");
    } else {
        let _ = w.navigate(window_url(&label, true, ""));
    }
}

/// Run one of the page's shortcuts (a combo from its SHORTCUTS table, e.g. "M:KeyN"): menu items and native keys.
fn route(app: &AppHandle, combo: &str) {
    if let Some(w) = front(app) {
        let _ = w.show();
        let _ = w.set_focus();
        let _ = w.eval(&format!("window.deskKey && deskKey({})", js_string(combo)));
    }
}

/// Menu item id → the page's combo.
const ROUTES: [(&str, &str); 10] = [
    ("settings", "M:Comma"),
    ("left", "M:Digit1"),
    ("new-session", "M:KeyN"),
    ("open", "M:KeyO"),
    ("search", "MS:KeyF"),
    ("week", "MS:KeyD"),
    ("schedules", "MC:KeyS"),
    ("calendar", "MC:KeyC"),
    ("meetings", "MS:KeyC"),
    ("shortcuts", "M:Slash"),
];

// ------------------------------------------------------------------ window frame and Dock badge
fn frame_path() -> PathBuf {
    state_dir().join("window.txt")
}

/// The other windows' frames: a line "label x y w h" each (the first window's is window.txt, as before).
fn windows_path() -> PathBuf {
    state_dir().join("windows.txt")
}

/// A frame (logical x y w h) that still lies on a connected display.
fn visible_frame(app: &AppHandle, v: &[f64]) -> Option<(f64, f64, f64, f64)> {
    if v.len() != 4 || v[2] < 480.0 || v[3] < 360.0 {
        return None;
    }
    let monitors = app.available_monitors().ok()?;
    let visible = monitors.iter().any(|m| {
        let sf = m.scale_factor();
        let (mx, my) = (m.position().x as f64 / sf, m.position().y as f64 / sf);
        let (mw, mh) = (m.size().width as f64 / sf, m.size().height as f64 / sf);
        v[0] + 100.0 > mx && v[0] < mx + mw - 100.0 && v[1] >= my - 10.0 && v[1] < my + mh - 100.0
    });
    visible.then(|| (v[0], v[1], v[2], v[3]))
}

fn saved_frame(app: &AppHandle) -> Option<(f64, f64, f64, f64)> {
    let text = fs::read_to_string(frame_path()).ok()?;
    let v: Vec<f64> = text.split_whitespace().filter_map(|x| x.parse().ok()).collect();
    visible_frame(app, &v)
}

/// The other windows open when the app last quit: (label, frame).
fn saved_windows(app: &AppHandle) -> Vec<(String, Option<(f64, f64, f64, f64)>)> {
    let text = fs::read_to_string(windows_path()).unwrap_or_default();
    text.lines()
        .filter_map(|l| {
            let mut it = l.split_whitespace();
            let label = it.next()?.to_string();
            let ok = label.starts_with('w') && label.len() <= 8 && label[1..].chars().all(|c| c.is_ascii_digit());
            let v: Vec<f64> = it.filter_map(|x| x.parse().ok()).collect();
            ok.then(|| (label, visible_frame(app, &v)))
        })
        .collect()
}

fn frame_of(w: &WebviewWindow) -> Option<(f64, f64, f64, f64)> {
    if w.is_fullscreen().unwrap_or(false) || w.is_minimized().unwrap_or(false) {
        return None;
    }
    let (Ok(pos), Ok(size), Ok(sf)) = (w.outer_position(), w.inner_size(), w.scale_factor()) else { return None };
    Some((pos.x as f64 / sf, pos.y as f64 / sf, size.width as f64 / sf, size.height as f64 / sf))
}

/// Every window's frame; the other windows' list is what reopens next time (`gone`: one closing now, left out).
fn save_frame(app: &AppHandle, gone: Option<&str>) {
    let _ = fs::create_dir_all(state_dir());
    let mut others = String::new();
    let mut ws: Vec<(String, WebviewWindow)> = app.webview_windows().into_iter().collect();
    ws.sort_by(|a, b| a.0.cmp(&b.0));
    for (label, w) in ws {
        if Some(label.as_str()) == gone {
            continue;
        }
        let f = frame_of(&w);
        if label == "main" {
            if let Some((x, y, wd, h)) = f {
                let _ = fs::write(frame_path(), format!("{x} {y} {wd} {h}\n"));
            }
        } else if w.is_visible().unwrap_or(true) {
            let (x, y, wd, h) = f.unwrap_or((0.0, 0.0, 0.0, 0.0));
            others.push_str(&format!("{label} {x} {y} {wd} {h}\n"));
        }
    }
    let _ = fs::write(windows_path(), others);
}

/// A window: the page's tab row is its title bar, the window buttons over its left end.
fn make_window(app: &AppHandle, label: &str, url: WebviewUrl, frame: Option<(f64, f64, f64, f64)>) -> tauri::Result<WebviewWindow> {
    let mut win = WebviewWindowBuilder::new(app, label, url).title(app_name()).min_inner_size(480.0, 360.0);
    // No native title bar: the page's tab row is the title bar (ui.md: "the title bar holds only the tabs"),
    // with the window buttons over its left end (the page leaves room: body.app #titlebar).
    #[cfg(target_os = "macos")]
    {
        win = win
            .title_bar_style(tauri::TitleBarStyle::Overlay)
            .hidden_title(true)
            .traffic_light_position(tauri::LogicalPosition::new(TRAFFIC_X, TRAFFIC_Y));
    }
    win = match frame {
        Some((x, y, w, h)) => win.position(x, y).inner_size(w, h),
        None => win.inner_size(1280.0, 860.0).center(),
    };
    let nav = app.clone();
    win = win.on_navigation(move |url| shell_request(&nav, url));
    // Tells the page it runs in the app (links go to the system browser via the server).
    win.initialization_script("window.__ORBIT_APP__ = true;").build()
}

/// ⌘⇧N (the page asks with its group): another window beside the one in front, on that group. Each window looks at a
/// group of its own; the tabs are the server's, shared. ⌘` (macOS) goes from one to the next.
fn new_window(app: &AppHandle, group: &str) {
    let ws = app.webview_windows();
    let Some(label) = (2..100).map(|n| format!("w{n}")).find(|l| !ws.contains_key(l)) else { return };
    let frame = front(app).and_then(|w| frame_of(&w)).map(|(x, y, w, h)| (x + 28.0, y + 28.0, w, h));
    let url = if target_open() { WebviewUrl::External(window_url(&label, false, group)) } else { WebviewUrl::App("index.html".into()) };
    if let Ok(w) = make_window(app, &label, url, frame) {
        let _ = w.set_focus();
    }
    save_frame(app, None);
}

/// GET a small JSON answer from the server the window shows (std only: one request every few seconds).
fn get_json(path: &str) -> Option<String> {
    let t = target();
    let mut s = TcpStream::connect_timeout(&t.addr()?, Duration::from_millis(800)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    let auth = if t.token.is_empty() { String::new() } else { format!("Authorization: Bearer {}\r\n", t.token) };
    write!(s, "GET {path} HTTP/1.0\r\nHost: {}:{}\r\n{auth}\r\n", t.host, t.port).ok()?;
    let mut out = String::new();
    s.read_to_string(&mut out).ok()?;
    out.split_once("\r\n\r\n").map(|(_, body)| body.to_string())
}

/// The page's unread count on the Dock icon, so a hidden window still says an answer arrived.
fn badge_loop(app: AppHandle) {
    thread::spawn(move || {
        let mut last: Option<i64> = None;
        loop {
            thread::sleep(Duration::from_secs(3));
            let n = get_json("/api/badge")
                .and_then(|b| b.split("\"count\":").nth(1).map(|x| x.trim().trim_end_matches('}').trim().to_string()))
                .and_then(|x| x.parse::<i64>().ok())
                .unwrap_or(0);
            if last != Some(n) {
                if let Some(w) = app.webview_windows().into_values().next() {  // (the Dock's: one for the app)
                    let _ = w.set_badge_count(if n > 0 { Some(n) } else { None });
                }
                last = Some(n);
            }
        }
    });
}

/// Restart the server this app owns (to load changed Python code), then reload the page.
fn restart_server(app: &AppHandle) {
    let app = app.clone();
    thread::spawn(move || {
        if !target().local {
            return reload(&app);  // a server on another machine: restart it there
        }
        let ours = app.state::<Server>().0.lock().unwrap().is_some();
        if !ours && server_pid().is_none() {
            return reload(&app);  // a server started elsewhere (no pid file): not ours to restart
        }
        for w in app.webview_windows().into_values() {
            let _ = w.eval("window.saveDraft && saveDraft()");
        }
        stop_any_server(&app);  // ours, or one an earlier launch left running
        let end = Instant::now() + Duration::from_secs(5);
        while port_open() && Instant::now() < end {
            thread::sleep(Duration::from_millis(100));
        }
        boot(app.clone());
    });
}

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let reload = MenuItem::with_id(app, "reload", "Reload UI", true, Some("CmdOrCtrl+Shift+R"))?;
    // ⌘` stays macOS's: the next window (new_window). The left panel is ⌘1.
    let left = MenuItem::with_id(app, "left", "Toggle Left Panel", true, Some("CmdOrCtrl+1"))?;
    let new_win = MenuItem::with_id(app, "new-window", "New Window", true, Some("CmdOrCtrl+Shift+N"))?;
    let settings = MenuItem::with_id(app, "settings", "Settings…", true, Some("CmdOrCtrl+,"))?;
    // ⌘Z / ⌘⇧Z go to the page (deskUndo): an editor uses its own history; native undo would corrupt it.
    let undo = MenuItem::with_id(app, "undo", "Undo", true, Some("CmdOrCtrl+Z"))?;
    let redo = MenuItem::with_id(app, "redo", "Redo", true, Some("CmdOrCtrl+Shift+Z"))?;
    // Routed to the page without accelerators: the page owns these keys (a finder's own key closes it, and so on).
    let item = |id: &str, label: &str| MenuItem::with_id(app, id, label, true, None::<&str>);
    let restart = MenuItem::with_id(app, "restart", "Restart Server", true, None::<&str>)?;
    // Every agent process ends with it: the page asks first (restartHarnessServer in commands.js).
    let restart_hs = MenuItem::with_id(app, "restart-hs", "Restart Harness Server…", true, None::<&str>)?;
    let quit_all = MenuItem::with_id(app, "quit-all", "Quit Orbit and Stop Agents", true, Some("CmdOrCtrl+Alt+Q"))?;
    let log = MenuItem::with_id(app, "log", "Show Server Log", true, None::<&str>)?;
    let connect = MenuItem::with_id(app, "connect", "Connect to Server…", true, None::<&str>)?;
    let sep = || PredefinedMenuItem::separator(app);
    let name = app_name();
    let app_menu = Submenu::with_items(
        app,
        &name,
        true,
        &[
            &PredefinedMenuItem::about(app, Some(&format!("About {name}")), None)?,
            &sep()?,
            &settings,
            &connect,
            &sep()?,
            &PredefinedMenuItem::hide(app, None)?,
            &PredefinedMenuItem::hide_others(app, None)?,
            &PredefinedMenuItem::show_all(app, None)?,
            &sep()?,
            &PredefinedMenuItem::quit(app, None)?,
            &quit_all,
        ],
    )?;
    let edit = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &undo,
            &redo,
            &sep()?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;
    let view = Submenu::with_items(
        app,
        "View",
        true,
        &[&left, &sep()?, &reload, &restart, &restart_hs, &log, &sep()?, &PredefinedMenuItem::fullscreen(app, None)?],
    )?;
    let file = Submenu::with_items(
        app,
        "File",
        true,
        &[
            &item("new-session", "New Session (⌘N)")?,
            &item("open", "Open… (⌘O)")?,
            &item("search", "Search History and Notes (⌘⇧F)")?,
            &sep()?,
            &item("week", "This Week's Note (⌘⇧D)")?,
            &item("schedules", "Schedules (⌘⌃S)")?,
            &item("calendar", "Calendar (⌘⌃C)")?,
            &item("meetings", "Today's Meetings (⌘⇧C)")?,
        ],
    )?;
    let window = Submenu::with_items(
        app,
        "Window",
        true,
        &[&new_win, &sep()?, &PredefinedMenuItem::minimize(app, None)?, &PredefinedMenuItem::maximize(app, None)?],
    )?;
    let help = Submenu::with_items(app, "Help", true, &[&item("shortcuts", "Keyboard Shortcuts (⌘/)")?])?;
    Menu::with_items(app, &[&app_menu, &file, &edit, &view, &window, &help])
}

fn drag_drop(window: &tauri::Window, e: &tauri::DragDropEvent) {
    let Some(w) = window.app_handle().get_webview_window(window.label()) else { return };  // (the window dropped on)
    let k = window.scale_factor().unwrap_or(1.0);
    let paths = |ps: &Vec<std::path::PathBuf>| {
        ps.iter().map(|p| js_string(&p.to_string_lossy())).collect::<Vec<_>>().join(",")
    };
    let js = match e {
        tauri::DragDropEvent::Enter { paths: ps, position } => format!(
            "{{type:'enter',paths:[{}],x:{},y:{}}}", paths(ps), position.x / k, position.y / k),
        tauri::DragDropEvent::Over { position } => format!("{{type:'over',x:{},y:{}}}", position.x / k, position.y / k),
        tauri::DragDropEvent::Drop { paths: ps, position } => format!(
            "{{type:'drop',paths:[{}],x:{},y:{}}}", paths(ps), position.x / k, position.y / k),
        tauri::DragDropEvent::Leave => "{type:'leave'}".to_string(),
        _ => return,
    };
    let _ = w.eval(&format!("window.orbitDrop && orbitDrop({js})"));
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Server(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            app.set_menu(build_menu(&handle)?)?;
            make_window(&handle, "main", WebviewUrl::App("index.html".into()), saved_frame(&handle))?;
            for (label, frame) in saved_windows(&handle) {  // (the other windows open at the last quit)
                let _ = make_window(&handle, &label, WebviewUrl::App("index.html".into()), frame);
            }
            boot(handle.clone());
            badge_loop(handle);
            Ok(())
        })
        .on_window_event(|window, event| match event {
            // Closing the first window hides it: the server keeps running agents and schedules. ⌘Q quits. Another
            // window closes for good (it is not reopened next time).
            WindowEvent::CloseRequested { api, .. } => {
                if window.label() == "main" {
                    save_frame(window.app_handle(), None);
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    save_frame(window.app_handle(), Some(window.label()));
                }
            }
            // Files dragged in from Finder: WKWebView never hands the page a drop (and a browser File has no path),
            // so the shell passes the real paths and where the pointer is (CSS px before the page's zoom).
            WindowEvent::DragDrop(e) => drag_drop(window, e),
            _ => {}
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "reload" => reload(app),
            "restart" => restart_server(app),
            "new-window" => {
                // The page asks with its group (newWindow, tabs.js); a window without its page: on no group.
                match front(app) {
                    Some(w) if w.url().map(|u| u.scheme() != "tauri").unwrap_or(false) => {
                        let _ = w.eval("window.newWindow ? newWindow() : (location.href = 'http://orbit-shell.invalid/new-window?url=')");
                    }
                    _ => {
                        let app = app.clone();
                        thread::spawn(move || new_window(&app, ""));
                    }
                }
            }
            "restart-hs" => {
                if let Some(w) = front(app) {
                    let _ = w.eval("window.restartHarnessServer && restartHarnessServer()");
                }
            }
            "connect" => {
                // The page's dialog when a page is up; the failure page has its own form.
                if let Some(w) = front(app) {
                    let _ = w.show();
                    let _ = w.eval("window.connectServer ? connectServer() : (window.orbitConnectForm && orbitConnectForm())");
                }
            }
            "quit-all" => {
                STOP_ON_EXIT.store(true, std::sync::atomic::Ordering::SeqCst);
                app.exit(0);
            }
            "undo" | "redo" => {
                if let Some(w) = front(app) {
                    let redo = event.id().as_ref() == "redo";
                    let _ = w.eval(&format!(
                        "window.deskUndo ? deskUndo({redo}) : document.execCommand('{}')",
                        if redo { "redo" } else { "undo" }
                    ));
                }
            }
            "log" => {
                let _ = Command::new("open").arg("-a").arg("Console").arg(log_path()).spawn();
            }
            id => {
                if let Some((_, combo)) = ROUTES.iter().find(|(k, _)| *k == id) {
                    route(app, combo);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Orbit app");

    app.run(|app, event| match event {
        RunEvent::ExitRequested { .. } => save_frame(app, None),
        RunEvent::Exit => {
            // The backend keeps running (agents, schedules); the next launch reattaches to it on its port.
            if STOP_ON_EXIT.load(std::sync::atomic::Ordering::SeqCst) && target().local {
                stop_with_agents(app);
                stop_any_server(app);  // (one that already exited: nothing left to do; else SIGTERM, then SIGKILL)
            }
        }
        // The Dock icon (or opening the app again) brings the hidden first window back when none is shown.
        RunEvent::Reopen { has_visible_windows, .. } => {
            if !has_visible_windows {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
        }
        _ => {}
    });
}
