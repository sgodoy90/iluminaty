use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager, State,
};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;

const DEFAULT_VLM_MODEL: &str = "HuggingFaceTB/SmolVLM2-500M-Instruct";

// ─── Shared State ───────────────────────────────────────────────────────────────

struct ServerProcess(Mutex<Option<Child>>);

struct DesktopSettingsState(Arc<Mutex<DesktopSettings>>);

struct VlmDownloadState(Arc<Mutex<VlmDownloadStatus>>);

#[derive(Serialize)]
struct ServerStatus {
    running: bool,
    pid: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
struct DesktopSettings {
    autonomy: String,
    operating_mode: String,
    monitor: i32,
    capture_interval_ms: u64,
    fast_loop_hz: f32,
    deep_loop_hz: f32,
    vision_profile: String,
    api_port: u16,
    vlm_enabled: bool,
    vlm_backend: String,
    vlm_model: String,
    vlm_int8: bool,
    vlm_device: String,
    vlm_image_size: u32,
    vlm_max_tokens: u32,
    vlm_min_interval_ms: u32,
    vlm_keepalive_ms: u32,
    vlm_priority_threshold: f32,
    vlm_secondary_heartbeat_s: f32,
}

impl Default for DesktopSettings {
    fn default() -> Self {
        Self {
            autonomy: "confirm".to_string(),
            operating_mode: "SAFE".to_string(),
            monitor: 0,
            capture_interval_ms: 500,
            fast_loop_hz: 10.0,
            deep_loop_hz: 1.0,
            vision_profile: "core_ram".to_string(),
            api_port: 8420,
            vlm_enabled: false,
            vlm_backend: "smol".to_string(),
            vlm_model: DEFAULT_VLM_MODEL.to_string(),
            vlm_int8: true,
            vlm_device: "auto".to_string(),
            vlm_image_size: 384,
            vlm_max_tokens: 64,
            vlm_min_interval_ms: 900,
            vlm_keepalive_ms: 7000,
            vlm_priority_threshold: 0.55,
            vlm_secondary_heartbeat_s: 8.0,
        }
    }
}

impl DesktopSettings {
    fn sanitize(mut self) -> Self {
        self.autonomy = match self.autonomy.to_lowercase().as_str() {
            "suggest" | "confirm" | "auto" => self.autonomy.to_lowercase(),
            _ => "confirm".to_string(),
        };
        self.operating_mode = match self.operating_mode.to_uppercase().as_str() {
            "SAFE" | "HYBRID" | "RAW" => self.operating_mode.to_uppercase(),
            _ => "SAFE".to_string(),
        };
        self.monitor = self.monitor.max(0);
        self.capture_interval_ms = self.capture_interval_ms.clamp(100, 10_000);
        self.fast_loop_hz = self.fast_loop_hz.clamp(2.0, 20.0);
        self.deep_loop_hz = self.deep_loop_hz.clamp(0.5, 4.0);
        self.vision_profile = match self.vision_profile.to_lowercase().as_str() {
            "core_ram" | "vision_plus" => self.vision_profile.to_lowercase(),
            _ => "core_ram".to_string(),
        };
        self.api_port = self.api_port.clamp(1024, 65535);
        self.vlm_backend = match self.vlm_backend.to_lowercase().as_str() {
            "smol" | "blip" => self.vlm_backend.to_lowercase(),
            _ => "smol".to_string(),
        };
        self.vlm_device = match self.vlm_device.to_lowercase().as_str() {
            "auto" | "cpu" | "cuda" | "gpu" => self.vlm_device.to_lowercase(),
            _ => "auto".to_string(),
        };
        if self.vlm_model.trim().is_empty() {
            self.vlm_model = DEFAULT_VLM_MODEL.to_string();
        }
        self.vlm_image_size = self.vlm_image_size.clamp(224, 768);
        self.vlm_max_tokens = self.vlm_max_tokens.clamp(16, 256);
        self.vlm_min_interval_ms = self.vlm_min_interval_ms.clamp(0, 15_000);
        self.vlm_keepalive_ms = self.vlm_keepalive_ms.clamp(500, 120_000);
        self.vlm_priority_threshold = self.vlm_priority_threshold.clamp(0.0, 1.0);
        self.vlm_secondary_heartbeat_s = self.vlm_secondary_heartbeat_s.clamp(1.0, 120.0);
        self
    }

    fn capture_fps(&self) -> f32 {
        let ms = self.capture_interval_ms.max(100);
        (1000.0f32 / ms as f32).clamp(0.5, 30.0)
    }

    fn required_modules(&self, force_vlm: bool) -> Vec<&'static str> {
        let mut mods = vec!["uvicorn", "mss", "fastapi", "numpy", "PIL"];
        if self.vlm_enabled || force_vlm {
            mods.extend(["torch", "transformers", "huggingface_hub"]);
        }
        mods
    }
}

#[derive(Debug, Clone, Serialize)]
struct RuntimeStatus {
    ready: bool,
    python: String,
    runtime_dir: String,
    model_cache_dir: String,
    missing: Vec<String>,
    message: String,
}

#[derive(Debug, Clone, Serialize)]
struct VlmDownloadStatus {
    status: String,
    model: String,
    message: String,
    started_at_ms: Option<u64>,
    finished_at_ms: Option<u64>,
}

impl Default for VlmDownloadStatus {
    fn default() -> Self {
        Self {
            status: "idle".to_string(),
            model: DEFAULT_VLM_MODEL.to_string(),
            message: "idle".to_string(),
            started_at_ms: None,
            finished_at_ms: None,
        }
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn app_data_dir() -> PathBuf {
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        return PathBuf::from(local).join("ILUMINATY");
    }
    std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".iluminaty")
}

fn runtime_root() -> PathBuf {
    app_data_dir().join("runtime")
}

fn model_cache_dir() -> PathBuf {
    app_data_dir().join("models").join("hf")
}

fn settings_file_path() -> PathBuf {
    app_data_dir().join("desktop-settings.json")
}

fn ensure_parent(path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("create_dir_all failed: {}", e))?;
    }
    Ok(())
}

fn write_settings_file(settings: &DesktopSettings) -> Result<(), String> {
    let path = settings_file_path();
    ensure_parent(&path)?;
    let json = serde_json::to_string_pretty(settings).map_err(|e| e.to_string())?;
    fs::write(path, json).map_err(|e| e.to_string())
}

fn load_settings_file() -> DesktopSettings {
    let path = settings_file_path();
    let settings = fs::read_to_string(&path)
        .ok()
        .and_then(|raw| serde_json::from_str::<DesktopSettings>(&raw).ok())
        .map(|s| s.sanitize())
        .unwrap_or_else(DesktopSettings::default);
    let _ = write_settings_file(&settings);
    settings
}

fn venv_python_path() -> PathBuf {
    let base = runtime_root().join("venv");
    #[cfg(target_os = "windows")]
    {
        base.join("Scripts").join("python.exe")
    }
    #[cfg(not(target_os = "windows"))]
    {
        base.join("bin").join("python")
    }
}

fn command_hidden(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(target_os = "windows")]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

fn command_ok(mut cmd: Command) -> Result<String, String> {
    let output = cmd.output().map_err(|e| e.to_string())?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        let err = String::from_utf8_lossy(&output.stderr).trim().to_string();
        Err(if err.is_empty() {
            format!("command failed with status {}", output.status)
        } else {
            err
        })
    }
}

fn python_candidates() -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        out.push(format!(r"{}\Microsoft\WindowsApps\python3.13.exe", local));
        out.push(format!(r"{}\Microsoft\WindowsApps\python3.12.exe", local));
        out.push(format!(r"{}\Programs\Python\Python313\python.exe", local));
        out.push(format!(r"{}\Programs\Python\Python312\python.exe", local));
    }
    out.push("py".to_string());
    out.push("python3".to_string());
    out.push("python".to_string());
    out
}

fn python_smoke(cmd: &str) -> bool {
    let mut c = command_hidden(cmd);
    c.args(["-c", "import sys;print(sys.version)"])
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    c.status().map(|s| s.success()).unwrap_or(false)
}

fn find_bootstrap_python() -> Option<String> {
    python_candidates()
        .into_iter()
        .find(|candidate| python_smoke(candidate))
}

fn runtime_missing_modules(python: &Path, modules: &[&str]) -> Result<Vec<String>, String> {
    let snippet = format!(
        "import importlib.util as u;mods={:?};missing=[m for m in mods if u.find_spec(m) is None];print(','.join(missing))",
        modules
    );
    let mut cmd = command_hidden(python);
    cmd.args(["-c", &snippet]);
    let stdout = command_ok(cmd)?;
    if stdout.trim().is_empty() {
        return Ok(vec![]);
    }
    Ok(stdout
        .split(',')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect())
}

fn pip_install(python: &Path, args: &[&str], cwd: Option<&Path>) -> Result<(), String> {
    let mut cmd = command_hidden(python);
    cmd.arg("-m").arg("pip").args(args);
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    command_ok(cmd).map(|_| ())
}

fn probe_runtime(settings: &DesktopSettings, force_vlm: bool) -> Result<RuntimeStatus, String> {
    let runtime_dir = runtime_root();
    let cache_dir = model_cache_dir();
    fs::create_dir_all(&runtime_dir).map_err(|e| format!("runtime dir error: {}", e))?;
    fs::create_dir_all(&cache_dir).map_err(|e| format!("model cache dir error: {}", e))?;

    let venv_python = venv_python_path();
    if !venv_python.exists() {
        return Ok(RuntimeStatus {
            ready: false,
            python: venv_python.to_string_lossy().to_string(),
            runtime_dir: runtime_dir.to_string_lossy().to_string(),
            model_cache_dir: cache_dir.to_string_lossy().to_string(),
            missing: settings
                .required_modules(force_vlm)
                .into_iter()
                .map(|m| m.to_string())
                .collect(),
            message: "runtime not initialized (bootstrap required)".to_string(),
        });
    }

    let required = settings.required_modules(force_vlm);
    let missing = runtime_missing_modules(&venv_python, &required)?;
    if missing.is_empty() {
        return Ok(RuntimeStatus {
            ready: true,
            python: venv_python.to_string_lossy().to_string(),
            runtime_dir: runtime_dir.to_string_lossy().to_string(),
            model_cache_dir: cache_dir.to_string_lossy().to_string(),
            missing: vec![],
            message: "runtime ready".to_string(),
        });
    }

    Ok(RuntimeStatus {
        ready: false,
        python: venv_python.to_string_lossy().to_string(),
        runtime_dir: runtime_dir.to_string_lossy().to_string(),
        model_cache_dir: cache_dir.to_string_lossy().to_string(),
        missing: missing.clone(),
        message: format!("Missing runtime modules: {}", missing.join(", ")),
    })
}

fn ensure_runtime(settings: &DesktopSettings, force_vlm: bool) -> Result<RuntimeStatus, String> {
    let runtime_dir = runtime_root();
    let cache_dir = model_cache_dir();
    fs::create_dir_all(&runtime_dir).map_err(|e| format!("runtime dir error: {}", e))?;
    fs::create_dir_all(&cache_dir).map_err(|e| format!("model cache dir error: {}", e))?;

    let venv_python = venv_python_path();
    if !venv_python.exists() {
        let base = find_bootstrap_python()
            .ok_or_else(|| "No Python runtime found to bootstrap ILUMINATY".to_string())?;
        let mut venv_cmd = command_hidden(base);
        venv_cmd
            .args(["-m", "venv"])
            .arg(runtime_dir.join("venv"));
        command_ok(venv_cmd).map_err(|e| format!("venv creation failed: {}", e))?;
    }

    let required = settings.required_modules(force_vlm);
    let missing_initial = runtime_missing_modules(&venv_python, &required)?;
    if missing_initial.is_empty() {
        return Ok(RuntimeStatus {
            ready: true,
            python: venv_python.to_string_lossy().to_string(),
            runtime_dir: runtime_dir.to_string_lossy().to_string(),
            model_cache_dir: cache_dir.to_string_lossy().to_string(),
            missing: vec![],
            message: "runtime ready".to_string(),
        });
    }

    let _ = pip_install(
        &venv_python,
        &["install", "--upgrade", "pip", "setuptools", "wheel"],
        None,
    );

    let root = PathBuf::from(project_root());
    let extras = if settings.vlm_enabled || force_vlm {
        "all"
    } else {
        "ocr"
    };

    let local_install = if root.join("pyproject.toml").exists() {
        pip_install(
            &venv_python,
            &["install", "-e", &format!(".[{}]", extras)],
            Some(&root),
        )
    } else {
        Err("local source not found".to_string())
    };

    if local_install.is_err() {
        pip_install(
            &venv_python,
            &["install", &format!("iluminaty[{}]", extras)],
            None,
        )?;
    }

    let missing_final = runtime_missing_modules(&venv_python, &required)?;
    if missing_final.is_empty() {
        Ok(RuntimeStatus {
            ready: true,
            python: venv_python.to_string_lossy().to_string(),
            runtime_dir: runtime_dir.to_string_lossy().to_string(),
            model_cache_dir: cache_dir.to_string_lossy().to_string(),
            missing: vec![],
            message: "runtime bootstrapped".to_string(),
        })
    } else {
        Ok(RuntimeStatus {
            ready: false,
            python: venv_python.to_string_lossy().to_string(),
            runtime_dir: runtime_dir.to_string_lossy().to_string(),
            model_cache_dir: cache_dir.to_string_lossy().to_string(),
            missing: missing_final.clone(),
            message: format!("Missing runtime modules: {}", missing_final.join(", ")),
        })
    }
}

fn download_vlm_model(
    python: &Path,
    model_id: &str,
    force_download: bool,
) -> Result<(), String> {
    let cache = model_cache_dir();
    fs::create_dir_all(&cache).map_err(|e| e.to_string())?;
    let script = r#"
import os
from huggingface_hub import snapshot_download
model = os.environ.get("ILUMINATY_VLM_MODEL")
cache = os.environ.get("HF_HOME")
snapshot_download(repo_id=model, cache_dir=cache, local_files_only=False)
print("ok")
"#;

    let mut cmd = command_hidden(python);
    cmd.arg("-c").arg(script);
    cmd.env("ILUMINATY_VLM_MODEL", model_id);
    cmd.env("HF_HOME", cache);
    if force_download {
        cmd.env("HF_HUB_FORCE_DOWNLOAD", "1");
    }
    command_ok(cmd).map(|_| ())
}

// ─── Commands ──────────────────────────────────────────────────────────────────

#[tauri::command]
fn get_desktop_settings(settings_state: State<DesktopSettingsState>) -> Result<DesktopSettings, String> {
    let guard = settings_state.0.lock().map_err(|e| e.to_string())?;
    Ok(guard.clone())
}

#[tauri::command]
fn save_desktop_settings(
    settings: DesktopSettings,
    settings_state: State<DesktopSettingsState>,
) -> Result<DesktopSettings, String> {
    let sanitized = settings.sanitize();
    write_settings_file(&sanitized)?;
    let mut guard = settings_state.0.lock().map_err(|e| e.to_string())?;
    *guard = sanitized.clone();
    Ok(sanitized)
}

#[tauri::command]
fn get_runtime_status(settings_state: State<DesktopSettingsState>) -> Result<RuntimeStatus, String> {
    let settings = settings_state.0.lock().map_err(|e| e.to_string())?.clone();
    probe_runtime(&settings, false)
}

#[tauri::command]
fn bootstrap_runtime(
    install_vlm: Option<bool>,
    settings_state: State<DesktopSettingsState>,
) -> Result<RuntimeStatus, String> {
    let mut settings = settings_state.0.lock().map_err(|e| e.to_string())?.clone();
    if install_vlm.unwrap_or(false) {
        settings.vlm_enabled = true;
    }
    let status = ensure_runtime(&settings, install_vlm.unwrap_or(false))?;
    if status.ready {
        write_settings_file(&settings)?;
        let mut guard = settings_state.0.lock().map_err(|e| e.to_string())?;
        *guard = settings;
    }
    Ok(status)
}

#[tauri::command]
fn get_vlm_download_status(vlm_state: State<VlmDownloadState>) -> Result<VlmDownloadStatus, String> {
    let status = vlm_state.0.lock().map_err(|e| e.to_string())?.clone();
    Ok(status)
}

#[tauri::command]
fn start_vlm_download(
    model_id: Option<String>,
    force: Option<bool>,
    settings_state: State<DesktopSettingsState>,
    vlm_state: State<VlmDownloadState>,
) -> Result<VlmDownloadStatus, String> {
    let mut settings = settings_state.0.lock().map_err(|e| e.to_string())?.clone();
    let model = model_id.unwrap_or_else(|| settings.vlm_model.clone());
    if model.trim().is_empty() {
        return Err("model_id is required".to_string());
    }

    settings.vlm_enabled = true;
    settings.vlm_model = model.clone();
    settings = settings.sanitize();
    write_settings_file(&settings)?;
    {
        let mut guard = settings_state.0.lock().map_err(|e| e.to_string())?;
        *guard = settings.clone();
    }

    {
        let mut status = vlm_state.0.lock().map_err(|e| e.to_string())?;
        if status.status == "running" {
            return Ok(status.clone());
        }
        *status = VlmDownloadStatus {
            status: "running".to_string(),
            model: model.clone(),
            message: "Preparing runtime...".to_string(),
            started_at_ms: Some(now_ms()),
            finished_at_ms: None,
        };
    }

    let status_arc = vlm_state.0.clone();
    let settings_for_thread = settings.clone();
    let force_download = force.unwrap_or(false);
    std::thread::spawn(move || {
        let update_status = |status: &str, message: &str, finished: bool| {
            if let Ok(mut lock) = status_arc.lock() {
                lock.status = status.to_string();
                lock.message = message.to_string();
                if finished {
                    lock.finished_at_ms = Some(now_ms());
                }
            }
        };

        match ensure_runtime(&settings_for_thread, true) {
            Ok(runtime) if runtime.ready => {
                update_status("running", "Downloading VLM model...", false);
                let python = PathBuf::from(runtime.python);
                match download_vlm_model(&python, &model, force_download) {
                    Ok(_) => update_status("done", "VLM model cached successfully", true),
                    Err(e) => update_status("error", &format!("VLM download failed: {}", e), true),
                }
            }
            Ok(runtime) => {
                update_status("error", &runtime.message, true);
            }
            Err(e) => {
                update_status("error", &format!("Runtime bootstrap failed: {}", e), true);
            }
        }
    });

    let status = vlm_state.0.lock().map_err(|e| e.to_string())?.clone();
    Ok(status)
}

#[tauri::command]
fn start_server(
    server_state: State<ServerProcess>,
    settings_state: State<DesktopSettingsState>,
) -> Result<String, String> {
    start_server_internal(&server_state, &settings_state)
}

#[tauri::command]
fn stop_server(server_state: State<ServerProcess>) -> Result<String, String> {
    let mut proc = server_state.0.lock().map_err(|e| e.to_string())?;
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
fn server_status(server_state: State<ServerProcess>) -> Result<ServerStatus, String> {
    let mut proc = server_state.0.lock().unwrap_or_else(|e| e.into_inner());
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
async fn api_get(
    endpoint: String,
    api_key: Option<String>,
    settings_state: State<'_, DesktopSettingsState>,
) -> Result<String, String> {
    if !endpoint.starts_with('/') || endpoint.contains("://") || endpoint.contains('@') {
        return Err("Invalid endpoint".into());
    }
    let port = settings_state
        .0
        .lock()
        .map_err(|e| e.to_string())?
        .api_port;
    let url = format!("http://127.0.0.1:{}{}", port, endpoint);
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
async fn api_post(
    endpoint: String,
    body: String,
    api_key: Option<String>,
    settings_state: State<'_, DesktopSettingsState>,
) -> Result<String, String> {
    if !endpoint.starts_with('/') || endpoint.contains("://") || endpoint.contains('@') {
        return Err("Invalid endpoint".into());
    }
    let port = settings_state
        .0
        .lock()
        .map_err(|e| e.to_string())?
        .api_port;
    let url = format!("http://127.0.0.1:{}{}", port, endpoint);
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
    let resp = req
        .send()
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
    let download_url = json["html_url"].as_str().unwrap_or("").to_string();
    Ok(UpdateInfo {
        update_available: latest != current && latest != "unknown",
        current,
        latest,
        download_url,
    })
}

// ─── API Key Management (OS Keyring) ─────────────────────────────────────────

const KEYRING_SERVICE: &str = "iluminaty";

fn keyring_id(provider: &str) -> String {
    format!("{}-api-key", provider.to_lowercase().trim())
}

fn validate_key_format(provider: &str, key: &str) -> Result<(), String> {
    let key = key.trim();
    if key.is_empty() {
        return Err("API key cannot be empty".to_string());
    }
    match provider {
        "anthropic" => {
            if !key.starts_with("sk-ant-") {
                return Err("Anthropic keys start with sk-ant-".to_string());
            }
        }
        "openai" => {
            if !key.starts_with("sk-") {
                return Err("OpenAI keys start with sk-".to_string());
            }
        }
        "gemini" => {
            if !key.starts_with("AIza") || key.len() < 30 {
                return Err("Gemini keys start with AIza and are 35+ chars".to_string());
            }
        }
        _ => {}
    }
    Ok(())
}

#[tauri::command]
async fn save_api_key(provider: String, key: String) -> Result<String, String> {
    let key = key.trim().to_string();
    validate_key_format(&provider, &key)?;

    // Test the key with a lightweight API call
    let client = reqwest::Client::new();
    let valid = match provider.as_str() {
        "anthropic" => {
            let resp = client
                .post("https://api.anthropic.com/v1/messages")
                .header("x-api-key", &key)
                .header("anthropic-version", "2023-06-01")
                .header("content-type", "application/json")
                .body(r#"{"model":"claude-sonnet-4-20250514","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}"#)
                .send()
                .await;
            match resp {
                Ok(r) => r.status().as_u16() != 401,
                Err(_) => false,
            }
        }
        "openai" => {
            let resp = client
                .get("https://api.openai.com/v1/models")
                .header("Authorization", format!("Bearer {}", &key))
                .send()
                .await;
            match resp {
                Ok(r) => r.status().as_u16() != 401,
                Err(_) => false,
            }
        }
        "gemini" => {
            let resp = client
                .get(format!(
                    "https://generativelanguage.googleapis.com/v1/models?key={}",
                    &key
                ))
                .send()
                .await;
            match resp {
                Ok(r) => r.status().is_success(),
                Err(_) => false,
            }
        }
        _ => true, // Unknown provider — skip validation
    };

    if !valid {
        return Err(format!("Invalid {} API key — authentication failed", provider));
    }

    // Store in OS keyring
    let entry = keyring::Entry::new(KEYRING_SERVICE, &keyring_id(&provider))
        .map_err(|e| format!("Keyring error: {}", e))?;
    entry
        .set_password(&key)
        .map_err(|e| format!("Failed to store key: {}", e))?;

    Ok(format!("{} key validated and saved", provider))
}

#[tauri::command]
async fn get_api_key(provider: String) -> Result<Option<String>, String> {
    let entry = keyring::Entry::new(KEYRING_SERVICE, &keyring_id(&provider))
        .map_err(|e| format!("Keyring error: {}", e))?;
    match entry.get_password() {
        Ok(key) => Ok(Some(key)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("Keyring read failed: {}", e)),
    }
}

#[tauri::command]
async fn delete_api_key(provider: String) -> Result<String, String> {
    let entry = keyring::Entry::new(KEYRING_SERVICE, &keyring_id(&provider))
        .map_err(|e| format!("Keyring error: {}", e))?;
    match entry.delete_credential() {
        Ok(_) => Ok(format!("{} key deleted", provider)),
        Err(keyring::Error::NoEntry) => Ok("No key to delete".to_string()),
        Err(e) => Err(format!("Delete failed: {}", e)),
    }
}

#[tauri::command]
async fn list_api_keys() -> Result<Vec<serde_json::Value>, String> {
    let providers = ["anthropic", "openai", "gemini"];
    let mut result = Vec::new();
    for p in &providers {
        let entry = keyring::Entry::new(KEYRING_SERVICE, &keyring_id(p))
            .map_err(|e| format!("Keyring error: {}", e))?;
        let has_key = entry.get_password().is_ok();
        result.push(serde_json::json!({
            "provider": p,
            "configured": has_key,
            "masked": if has_key {
                match entry.get_password() {
                    Ok(k) if k.len() > 8 => format!("{}...{}", &k[..4], &k[k.len()-4..]),
                    Ok(k) => format!("{}...", &k[..2]),
                    Err(_) => "***".to_string(),
                }
            } else {
                "".to_string()
            },
        }));
    }
    Ok(result)
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

fn spawn_server(python: &Path, settings: &DesktopSettings) -> Result<Child, String> {
    let root = project_root();
    let fps = settings.capture_fps().to_string();
    let monitor = settings.monitor.to_string();
    let fast_loop = settings.fast_loop_hz.to_string();
    let deep_loop = settings.deep_loop_hz.to_string();
    let port = settings.api_port.to_string();

    let mut cmd = command_hidden(python);
    cmd.args([
        "-m",
        "iluminaty.main",
        "start",
        "--actions",
        "--autonomy",
        &settings.autonomy,
        "--monitor",
        &monitor,
        "--fps",
        &fps,
        "--fast-loop-hz",
        &fast_loop,
        "--deep-loop-hz",
        &deep_loop,
        "--vision-profile",
        &settings.vision_profile,
        "--port",
        &port,
    ])
    .env("ILUMINATY_OPERATING_MODE", settings.operating_mode.clone())
    .current_dir(&root)
    .stdout(Stdio::null())
    .stderr(Stdio::null());

    if settings.vlm_enabled {
        cmd.env("ILUMINATY_VLM_CAPTION", "1");
        cmd.env("ILUMINATY_VLM_BACKEND", settings.vlm_backend.clone());
        cmd.env("ILUMINATY_VLM_MODEL", settings.vlm_model.clone());
        cmd.env("ILUMINATY_VLM_INT8", if settings.vlm_int8 { "1" } else { "0" });
        cmd.env("ILUMINATY_VLM_DEVICE", settings.vlm_device.clone());
        cmd.env("ILUMINATY_VLM_MODE", "on_demand");
        cmd.env("ILUMINATY_VLM_DTYPE", "auto");
        cmd.env("ILUMINATY_VLM_IMAGE_SIZE", settings.vlm_image_size.to_string());
        cmd.env("ILUMINATY_VLM_MAX_TOKENS", settings.vlm_max_tokens.to_string());
        cmd.env(
            "ILUMINATY_VLM_MIN_INTERVAL_MS",
            settings.vlm_min_interval_ms.to_string(),
        );
        cmd.env(
            "ILUMINATY_VLM_KEEPALIVE_MS",
            settings.vlm_keepalive_ms.to_string(),
        );
        cmd.env(
            "ILUMINATY_VLM_PRIORITY_THRESHOLD",
            format!("{:.3}", settings.vlm_priority_threshold),
        );
        cmd.env(
            "ILUMINATY_VLM_SECONDARY_HEARTBEAT_S",
            format!("{:.2}", settings.vlm_secondary_heartbeat_s),
        );
        cmd.env("HF_HOME", model_cache_dir().to_string_lossy().to_string());
    } else {
        cmd.env("ILUMINATY_VLM_CAPTION", "0");
    }

    // Keep compatibility with current test/development key flow.
    let key = std::env::var("ILUMINATY_KEY")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| "".to_string());
    cmd.env("ILUMINATY_KEY", key);

    cmd.spawn()
        .map_err(|e| format!("Failed to start server: {}", e))
}

fn project_root() -> String {
    if let Ok(root) = std::env::var("ILUMINATY_ROOT") {
        let p = PathBuf::from(&root);
        if p.join("iluminaty").join("main.py").exists() {
            return root;
        }
    }

    let exe = std::env::current_exe().unwrap_or_default();
    let mut path = exe
        .parent()
        .unwrap_or(std::path::Path::new("."))
        .to_path_buf();
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

    std::env::current_dir()
        .unwrap_or_default()
        .to_string_lossy()
        .to_string()
}

fn start_server_internal(
    server_state: &State<ServerProcess>,
    settings_state: &State<DesktopSettingsState>,
) -> Result<String, String> {
    let mut proc = server_state.0.lock().map_err(|e| e.to_string())?;
    if proc.is_some() {
        return Ok("Server already running".into());
    }

    let settings = settings_state.0.lock().map_err(|e| e.to_string())?.clone();
    let runtime = ensure_runtime(&settings, settings.vlm_enabled)?;
    if !runtime.ready {
        return Err(runtime.message);
    }
    let python = PathBuf::from(runtime.python.clone());
    let child = spawn_server(&python, &settings)?;
    let pid = child.id();
    *proc = Some(child);
    Ok(format!("Server started (PID: {})", pid))
}

fn stop_server_internal(server_state: &State<ServerProcess>) -> Result<(), String> {
    let mut proc = server_state.0.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut child) = *proc {
        let _ = child.kill();
        let _ = child.wait();
    }
    *proc = None;
    Ok(())
}

// ─── App ───────────────────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let settings = load_settings_file();
    let dl_status = VlmDownloadStatus {
        model: settings.vlm_model.clone(),
        ..VlmDownloadStatus::default()
    };

    tauri::Builder::default()
        .manage(ServerProcess(Mutex::new(None)))
        .manage(DesktopSettingsState(Arc::new(Mutex::new(settings))))
        .manage(VlmDownloadState(Arc::new(Mutex::new(dl_status))))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            get_desktop_settings,
            save_desktop_settings,
            get_runtime_status,
            bootstrap_runtime,
            start_vlm_download,
            get_vlm_download_status,
            start_server,
            stop_server,
            server_status,
            api_get,
            api_post,
            check_for_updates,
            save_api_key,
            get_api_key,
            delete_api_key,
            list_api_keys,
        ])
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "Show ILUMINATY", true, None::<&str>)?;
            let start = MenuItem::with_id(app, "start_srv", "Start Server", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop_srv", "Stop Server", true, None::<&str>)?;
            let check_update =
                MenuItem::with_id(app, "check_update", "Check for Updates", true, None::<&str>)?;
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
                        let server_state: State<ServerProcess> = app.state();
                        let settings_state: State<DesktopSettingsState> = app.state();
                        let _ = start_server_internal(&server_state, &settings_state);
                    }
                    "stop_srv" => {
                        let server_state: State<ServerProcess> = app.state();
                        let _ = stop_server_internal(&server_state);
                    }
                    "check_update" => {
                        let _ = app.emit("check-updates", ());
                    }
                    "quit" => {
                        let server_state: State<ServerProcess> = app.state();
                        let _ = stop_server_internal(&server_state);
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let server_state: State<ServerProcess> = window.state();
                let _ = stop_server_internal(&server_state);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ILUMINATY");
}
