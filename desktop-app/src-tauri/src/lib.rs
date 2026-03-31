use serde::Serialize;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager, State,
};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

// ─── State ───
struct ServerProcess(Mutex<Option<Child>>);

#[derive(Serialize)]
struct ServerStatus {
    running: bool,
    pid: Option<u32>,
}

// ─── Commands ───

#[tauri::command]
fn start_server(state: State<ServerProcess>) -> Result<String, String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if proc.is_some() {
        return Ok("Server already running".into());
    }

    let child = spawn_server()?;
    let pid = child.id();
    *proc = Some(child);
    Ok(format!("Server started (PID: {})", pid))
}

#[tauri::command]
fn stop_server(state: State<ServerProcess>) -> Result<String, String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut child) = *proc {
        child.kill().map_err(|e| e.to_string())?;
        let _ = child.wait();
        *proc = None;
        Ok("Server stopped".into())
    } else {
        Ok("Server not running".into())
    }
}

#[tauri::command]
fn server_status(state: State<ServerProcess>) -> Result<ServerStatus, String> {
    let mut proc = state.0.lock().unwrap_or_else(|e| e.into_inner());
    let exited = if let Some(ref mut child) = *proc {
        matches!(child.try_wait(), Ok(Some(_)))
    } else {
        false
    };
    if exited {
        *proc = None;
    }
    match &*proc {
        Some(child) => Ok(ServerStatus {
            running: true,
            pid: Some(child.id()),
        }),
        None => Ok(ServerStatus {
            running: false,
            pid: None,
        }),
    }
}

#[tauri::command]
async fn api_get(endpoint: String, api_key: Option<String>) -> Result<String, String> {
    if !endpoint.starts_with('/') || endpoint.contains("://") || endpoint.contains('@') {
        return Err("Invalid endpoint".into());
    }
    let url = format!("http://127.0.0.1:8420{}", endpoint);
    let client = reqwest::Client::new();
    let mut req = client.get(&url);
    if let Some(key) = api_key {
        if !key.is_empty() {
            req = req.header("x-api-key", key);
        }
    }
    let resp = req
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;
    resp.text()
        .await
        .map_err(|e| format!("Read failed: {}", e))
}

#[tauri::command]
async fn api_post(endpoint: String, body: String, api_key: Option<String>) -> Result<String, String> {
    if !endpoint.starts_with('/') || endpoint.contains("://") || endpoint.contains('@') {
        return Err("Invalid endpoint".into());
    }
    let url = format!("http://127.0.0.1:8420{}", endpoint);
    let client = reqwest::Client::new();
    let mut req = client
        .post(&url)
        .header("Content-Type", "application/json")
        .body(body);
    if let Some(key) = api_key {
        if !key.is_empty() {
            req = req.header("x-api-key", key);
        }
    }
    let resp = req.send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;
    resp.text()
        .await
        .map_err(|e| format!("Read failed: {}", e))
}

#[derive(Serialize)]
struct UpdateInfo {
    current: String,
    latest: String,
    update_available: bool,
    download_url: String,
}

#[tauri::command]
async fn check_for_updates() -> Result<UpdateInfo, String> {
    let url = "https://api.github.com/repos/sgodoy90/iluminaty/releases/latest";
    let client = reqwest::Client::new();
    let resp = client
        .get(url)
        .header("User-Agent", "ILUMINATY-Desktop/1.0.0")
        .send()
        .await
        .map_err(|e| format!("Check failed: {}", e))?;
    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("Parse failed: {}", e))?;
    let latest = json["tag_name"]
        .as_str()
        .unwrap_or("unknown")
        .trim_start_matches('v')
        .to_string();
    let current = env!("CARGO_PKG_VERSION").to_string();
    let download_url = json["html_url"]
        .as_str()
        .unwrap_or("")
        .to_string();
    Ok(UpdateInfo {
        update_available: latest != current && latest != "unknown",
        current,
        latest,
        download_url,
    })
}

// ─── Helpers ───

/// Spawn the Python server with hidden console window on Windows
fn spawn_server() -> Result<Child, String> {
    let python = find_python();
    let root = project_root();

    let mut cmd = Command::new(&python);
    cmd.args([
            "-m", "iluminaty.main", "start",
            "--actions",
            "--autonomy", "confirm",
            "--monitor", "0",
            "--fps", "5",
            "--fast-loop-hz", "10",
            "--deep-loop-hz", "1.0",
            "--vision-profile", "core_ram",
        ])
        .current_dir(&root)
        .env("ILUMINATY_KEY", "ILUM-dev-godo-master-key-2026")
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    // Hide the console window on Windows
    #[cfg(target_os = "windows")]
    cmd.creation_flags(CREATE_NO_WINDOW);

    cmd.spawn().map_err(|e| format!("Failed to start server: {}", e))
}

fn find_python() -> String {
    // Known full paths first (most reliable on Windows)
    let full_paths = [
        r"C:\Users\jgodo\AppData\Local\Microsoft\WindowsApps\python3.13.exe",
        r"C:\Users\jgodo\AppData\Local\Programs\Python\Python313\python.exe",
        r"C:\Users\jgodo\AppData\Local\Programs\Python\Python312\python.exe",
    ];
    for p in &full_paths {
        if std::path::Path::new(p).exists() && python_has_deps(p) {
            return p.to_string();
        }
    }
    // Then try PATH commands, but verify deps
    for cmd in &["python3", "python", "py"] {
        if python_has_deps(cmd) {
            return cmd.to_string();
        }
    }
    "python".into()
}

fn python_has_deps(cmd: &str) -> bool {
    let mut check = Command::new(cmd);
    check.args(["-c", "import uvicorn, mss"])
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(target_os = "windows")]
    check.creation_flags(CREATE_NO_WINDOW);
    matches!(check.output(), Ok(o) if o.status.success())
}

fn project_root() -> String {
    // 1. Check env var (set by installer or shortcut)
    if let Ok(root) = std::env::var("ILUMINATY_ROOT") {
        let p = std::path::PathBuf::from(&root);
        if p.join("iluminaty").join("main.py").exists() {
            return root;
        }
    }

    // 2. Walk up from exe location
    let exe = std::env::current_exe().unwrap_or_default();
    let mut path = exe.parent().unwrap_or(std::path::Path::new(".")).to_path_buf();
    for _ in 0..7 {
        if path.join("iluminaty").join("main.py").exists() {
            return path.to_string_lossy().to_string();
        }
        if let Some(parent) = path.parent() {
            path = parent.to_path_buf();
        } else {
            break;
        }
    }

    // 3. Walk up from current working directory
    let mut cwd = std::env::current_dir().unwrap_or_default();
    for _ in 0..5 {
        if cwd.join("iluminaty").join("main.py").exists() {
            return cwd.to_string_lossy().to_string();
        }
        if let Some(parent) = cwd.parent() {
            cwd = parent.to_path_buf();
        } else {
            break;
        }
    }

    // 4. Known dev path fallback
    let dev = std::path::PathBuf::from(r"C:\Users\jgodo\Desktop\iluminaty");
    if dev.join("iluminaty").join("main.py").exists() {
        return dev.to_string_lossy().to_string();
    }

    std::env::current_dir()
        .unwrap_or_default()
        .to_string_lossy()
        .to_string()
}

fn start_server_internal(state: &State<ServerProcess>) -> Result<(), String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if proc.is_some() {
        return Ok(());
    }
    let child = spawn_server()?;
    *proc = Some(child);
    Ok(())
}

fn stop_server_internal(state: &State<ServerProcess>) -> Result<(), String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut child) = *proc {
        let _ = child.kill();
        let _ = child.wait();
    }
    *proc = None;
    Ok(())
}

// ─── App ───

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ServerProcess(Mutex::new(None)))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            start_server,
            stop_server,
            server_status,
            api_get,
            api_post,
            check_for_updates,
        ])
        .setup(|app| {
            // ── System Tray (single icon) ──
            let show = MenuItem::with_id(app, "show", "Show ILUMINATY", true, None::<&str>)?;
            let start = MenuItem::with_id(app, "start_srv", "Start Server", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop_srv", "Stop Server", true, None::<&str>)?;
            let check_update = MenuItem::with_id(app, "check_update", "Check for Updates", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

            let menu = Menu::with_items(app, &[&show, &start, &stop, &check_update, &quit])?;

            TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .tooltip("ILUMINATY — Eye of God")
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "start_srv" => {
                        let state: State<ServerProcess> = app.state();
                        let _ = start_server_internal(&state);
                    }
                    "stop_srv" => {
                        let state: State<ServerProcess> = app.state();
                        let _ = stop_server_internal(&state);
                    }
                    "check_update" => {
                        let _ = app.emit("check-updates", ());
                    }
                    "quit" => {
                        let state: State<ServerProcess> = app.state();
                        let _ = stop_server_internal(&state);
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            // Server does NOT auto-start — frontend calls start_server after login
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let state: State<ServerProcess> = window.state();
                let _ = stop_server_internal(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ILUMINATY");
}
