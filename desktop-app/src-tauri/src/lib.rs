use serde::Serialize;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, State,
};

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

    // Find python executable
    let python = find_python();

    let child = Command::new(&python)
        .args(["-m", "iluminaty.main", "start", "--actions", "--autonomy", "confirm"])
        .current_dir(project_root())
        .spawn()
        .map_err(|e| format!("Failed to start server: {}", e))?;

    let pid = child.id();
    *proc = Some(child);
    Ok(format!("Server started (PID: {})", pid))
}

#[tauri::command]
fn stop_server(state: State<ServerProcess>) -> Result<String, String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut child) = *proc {
        child.kill().map_err(|e| e.to_string())?;
        *proc = None;
        Ok("Server stopped".into())
    } else {
        Ok("Server not running".into())
    }
}

#[tauri::command]
fn server_status(state: State<ServerProcess>) -> ServerStatus {
    let proc = state.0.lock().unwrap();
    match &*proc {
        Some(child) => ServerStatus {
            running: true,
            pid: Some(child.id()),
        },
        None => ServerStatus {
            running: false,
            pid: None,
        },
    }
}

#[tauri::command]
async fn api_get(endpoint: String) -> Result<String, String> {
    let url = format!("http://127.0.0.1:8420{}", endpoint);
    let resp = reqwest::get(&url)
        .await
        .map_err(|e| format!("Request failed: {}", e))?;
    resp.text()
        .await
        .map_err(|e| format!("Read failed: {}", e))
}

#[tauri::command]
async fn api_post(endpoint: String, body: String) -> Result<String, String> {
    let url = format!("http://127.0.0.1:8420{}", endpoint);
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .body(body)
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;
    resp.text()
        .await
        .map_err(|e| format!("Read failed: {}", e))
}

// ─── Helpers ───

fn find_python() -> String {
    // Try python, python3, py in order
    for cmd in &["python", "python3", "py"] {
        if Command::new(cmd).arg("--version").output().is_ok() {
            return cmd.to_string();
        }
    }
    "python".into()
}

fn project_root() -> String {
    // Go up from the desktop-app/src-tauri to the iluminaty root
    let exe = std::env::current_exe().unwrap_or_default();
    let mut path = exe.parent().unwrap_or(std::path::Path::new(".")).to_path_buf();
    // In dev mode, try the repo root
    for _ in 0..5 {
        if path.join("iluminaty").join("main.py").exists() {
            return path.to_string_lossy().to_string();
        }
        if let Some(parent) = path.parent() {
            path = parent.to_path_buf();
        } else {
            break;
        }
    }
    // Fallback: current directory
    std::env::current_dir()
        .unwrap_or_default()
        .to_string_lossy()
        .to_string()
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
        ])
        .setup(|app| {
            // ── System Tray ──
            let show = MenuItem::with_id(app, "show", "Show ILUMINATY", true, None::<&str>)?;
            let start = MenuItem::with_id(app, "start_srv", "Start Server", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop_srv", "Stop Server", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

            let menu = Menu::with_items(app, &[&show, &start, &stop, &quit])?;

            TrayIconBuilder::new()
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
                    "quit" => {
                        // Stop server before quitting
                        let state: State<ServerProcess> = app.state();
                        let _ = stop_server_internal(&state);
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // Stop server on window close
                let state: State<ServerProcess> = window.state();
                let _ = stop_server_internal(&state);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ILUMINATY");
}

fn start_server_internal(state: &State<ServerProcess>) -> Result<(), String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if proc.is_some() {
        return Ok(());
    }
    let python = find_python();
    let child = Command::new(&python)
        .args(["-m", "iluminaty.main", "start", "--actions", "--autonomy", "confirm"])
        .current_dir(project_root())
        .spawn()
        .map_err(|e| e.to_string())?;
    *proc = Some(child);
    Ok(())
}

fn stop_server_internal(state: &State<ServerProcess>) -> Result<(), String> {
    let mut proc = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut child) = *proc {
        let _ = child.kill();
    }
    *proc = None;
    Ok(())
}
