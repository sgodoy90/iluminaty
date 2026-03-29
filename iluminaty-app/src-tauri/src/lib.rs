use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager,
};

#[tauri::command]
fn get_api_url() -> String {
    "http://127.0.0.1:8420".to_string()
}

#[tauri::command]
fn get_version() -> String {
    "0.5.0".to_string()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![get_api_url, get_version])
        .setup(|app| {
            let show = MenuItem::with_id(app, "show", "Show Dashboard", true, None::<&str>)?;
            let hide = MenuItem::with_id(app, "hide", "Hide", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit ILUMINATY", true, None::<&str>)?;

            let menu = Menu::with_items(app, &[&show, &hide, &quit])?;

            TrayIconBuilder::with_id("iluminaty-tray")
                .tooltip("ILUMINATY - AI Vision Active")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "hide" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.hide();
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running ILUMINATY");
}
