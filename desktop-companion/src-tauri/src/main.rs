#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{fs, path::{Path, PathBuf}, process::Command};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri_plugin_deep_link::DeepLinkExt;
use url::Url;

const SETTINGS_FILE: &str = "desktop-companion.json";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct Settings {
    core_api_url: String,
    litemanager_path: String,
    dameware_path: String,
    rdp_path: String,
}

#[derive(Debug, Deserialize)]
struct ClaimResponse {
    launch_id: String,
    host: String,
    client: String,
    completion_token: String,
}

#[derive(Debug, Serialize)]
struct ClaimRequest<'a> { token: &'a str }

#[derive(Debug, Serialize)]
struct ResultRequest<'a> {
    launch_id: &'a str,
    completion_token: &'a str,
    status: &'a str,
    error_message: Option<&'a str>,
}

fn config_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    Ok(dir.join(SETTINGS_FILE))
}

fn load_settings(app: &tauri::AppHandle) -> Result<Settings, String> {
    let path = config_path(app)?;
    if !path.exists() { return Ok(Settings::default()); }
    serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

#[tauri::command]
fn get_settings(app: tauri::AppHandle) -> Result<Settings, String> { load_settings(&app) }

#[tauri::command]
fn save_settings(app: tauri::AppHandle, settings: Settings) -> Result<(), String> {
    if !settings.core_api_url.is_empty() {
        let parsed = Url::parse(&settings.core_api_url).map_err(|_| "Некорректный URL Core API".to_string())?;
        if !matches!(parsed.scheme(), "https" | "http") { return Err("Core API должен использовать HTTP(S)".to_string()); }
    }
    let path = config_path(&app)?;
    fs::write(path, serde_json::to_vec_pretty(&settings).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

fn is_safe_host(host: &str) -> bool {
    !host.is_empty() && host.len() <= 255 && host.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-'))
}

fn configured_path<'a>(settings: &'a Settings, client: &str) -> Result<&'a str, String> {
    let path = match client {
        "litemanager" => &settings.litemanager_path,
        "dameware" => &settings.dameware_path,
        "rdp" => &settings.rdp_path,
        _ => return Err("Неразрешённый клиент".to_string()),
    };
    if path.is_empty() || !Path::new(path).is_file() { return Err("Путь к выбранному клиенту не настроен".to_string()); }
    Ok(path)
}

fn launch_client(settings: &Settings, client: &str, host: &str) -> Result<(), String> {
    if !is_safe_host(host) { return Err("Некорректное имя хоста".to_string()); }
    let executable = configured_path(settings, client)?;
    let mut command = Command::new(executable);
    command.args(client_args(client, host)?);
    command.spawn().map(|_| ()).map_err(|e| format!("Не удалось запустить клиент: {e}"))
}

fn client_args(client: &str, host: &str) -> Result<Vec<String>, String> {
    if !is_safe_host(host) { return Err("Некорректное имя хоста".to_string()); }
    match client {
        "litemanager" => Ok(vec![format!("/connect:{host}")]),
        "dameware" => Ok(vec!["-c:".into(), format!("-m:{host}")]),
        "rdp" => Ok(vec![format!("/v:{host}")]),
        _ => Err("Неразрешённый клиент".to_string()),
    }
}

fn report_result(settings: &Settings, claim: &ClaimResponse, status: &str, error: Option<&str>) {
    let url = format!("{}/api/v1/desktop/launches/result", settings.core_api_url.trim_end_matches('/'));
    let _ = ureq::post(&url).send_json(json!(ResultRequest {
        launch_id: &claim.launch_id, completion_token: &claim.completion_token, status, error_message: error,
    }));
}

fn handle_deep_link(app: tauri::AppHandle, url: Url) {
    let token = match (url.scheme(), url.host_str(), url.query_pairs().find(|(k, _)| k == "token")) {
        ("intralink", Some("launch"), Some((_, token))) if token.len() >= 32 => token.into_owned(),
        _ => return,
    };
    std::thread::spawn(move || {
        let settings = match load_settings(&app) { Ok(value) if !value.core_api_url.is_empty() => value, _ => return };
        let claim_url = format!("{}/api/v1/desktop/launches/claim", settings.core_api_url.trim_end_matches('/'));
        let response = match ureq::post(&claim_url).send_json(json!(ClaimRequest { token: &token })) {
            Ok(value) => value,
            Err(_) => return,
        };
        let claim: ClaimResponse = match response.into_json() {
            Ok(value) => value,
            Err(_) => return,
        };
        match launch_client(&settings, &claim.client, &claim.host) {
            Ok(()) => report_result(&settings, &claim, "launched", None),
            Err(error) => report_result(&settings, &claim, "failed", Some(&error)),
        }
    });
}

fn open_settings(app: &tauri::AppHandle) {
    if app.get_webview_window("settings").is_none() {
        let _ = WebviewWindowBuilder::new(app, "settings", WebviewUrl::App("settings.html".into()))
            .title("IntraLink Desktop Companion")
            .inner_size(560.0, 430.0)
            .build();
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|_, _, _| {}))
        .plugin(tauri_plugin_deep_link::init())
        .invoke_handler(tauri::generate_handler![get_settings, save_settings])
        .setup(|app| {
            #[cfg(windows)]
            app.deep_link().register_all()?;
            let open = MenuItem::with_id(app, "settings", "Настройки", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            let app_handle = app.handle().clone();
            TrayIconBuilder::new().menu(&menu).on_menu_event(move |_, event| match event.id.as_ref() {
                "settings" => open_settings(&app_handle),
                "quit" => app_handle.exit(0),
                _ => {},
            }).build(app)?;
            let app_handle = app.handle().clone();
            if let Some(urls) = app.deep_link().get_current()? {
                for url in urls { handle_deep_link(app_handle.clone(), url); }
            }
            let listener_handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() { handle_deep_link(listener_handle.clone(), url.clone()); }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running IntraLink Desktop Companion");
}

#[cfg(test)]
mod tests {
    use super::{client_args, is_safe_host};
    #[test]
    fn accepts_only_safe_hosts() {
        assert!(is_safe_host("NTEMW0144"));
        assert!(is_safe_host("10.244.1.25"));
        assert!(!is_safe_host("PC-1 & powershell"));
    }

    #[test]
    fn builds_fixed_client_arguments() {
        assert_eq!(client_args("litemanager", "NTEMW0144").unwrap(), ["/connect:NTEMW0144"]);
        assert_eq!(client_args("dameware", "NTEMW0144").unwrap(), ["-c:", "-m:NTEMW0144"]);
        assert!(client_args("powershell", "NTEMW0144").is_err());
    }
}
