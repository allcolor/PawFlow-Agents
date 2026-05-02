#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

use serde_json::Value;
use std::cell::RefCell;
use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::rc::Rc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use slint::{CloseRequestResponse, ComponentHandle, Timer, TimerMode};
use tray_icon::{
    menu::{Menu, MenuEvent, MenuItem},
    Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent,
};

slint::include_modules!();

type RelayProcesses = Arc<Mutex<HashMap<String, Child>>>;

fn main() -> Result<(), slint::PlatformError> {
    let ui = AppWindow::new()?;
    let relays: RelayProcesses = Arc::new(Mutex::new(HashMap::new()));

    wire_refresh(&ui, Arc::clone(&relays));
    wire_server_actions(&ui, Arc::clone(&relays));
    wire_relay_actions(&ui, Arc::clone(&relays));
    wire_image_actions(&ui, Arc::clone(&relays));
    wire_path_picker(&ui);
    wire_log_actions(&ui);
    wire_navigation(&ui);
    let _tray_state = schedule_tray_setup(&ui);

    refresh_ui(&ui.as_weak(), &relays);
    ui.show()?;
    slint::run_event_loop_until_quit()
}

fn wire_refresh(ui: &AppWindow, relays: RelayProcesses) {
    let ui_weak = ui.as_weak();
    ui.on_refresh_state(move || {
        let ui_weak = ui_weak.clone();
        let relays = Arc::clone(&relays);
        run_background(ui_weak, "Refreshing...", move |ui| {
            refresh_ui(&ui, &relays);
        });
    });
}

fn wire_server_actions(ui: &AppWindow, relays: RelayProcesses) {
    let ui_weak = ui.as_weak();
    let relays_for_save = Arc::clone(&relays);
    ui.on_save_server(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let name = ui.get_server_name().to_string();
        let url = ui.get_server_url().to_string();
        let gateway_key = ui.get_gateway_key().to_string();
        let ui_weak = ui.as_weak();
        let relays = Arc::clone(&relays_for_save);
        run_background(ui_weak, "Saving server...", move |ui| {
            match add_server(&name, &url, &gateway_key) {
                Ok(_) => append_log(&ui, &format!("[server:{name}] saved\n")),
                Err(err) => {
                    append_log(&ui, &format!("[server:{name}] save failed: {err}\n"));
                    set_status(&ui, "Server save failed");
                    return;
                }
            }
            refresh_ui(&ui, &relays);
        });
    });

    let ui_weak = ui.as_weak();
    let relays_for_login = Arc::clone(&relays);
    ui.on_login_server(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let name = ui.get_server_name().to_string();
        let ui_weak = ui.as_weak();
        let relays = Arc::clone(&relays_for_login);
        run_background(ui_weak, "Login...", move |ui| {
            match login_server(&name, &ui) {
                Ok(_) => append_log(&ui, &format!("[server:{name}] login/status completed\n")),
                Err(err) => {
                    append_log(&ui, &format!("[server:{name}] login failed: {err}\n"));
                    set_status(&ui, "Login failed");
                    return;
                }
            }
            refresh_ui(&ui, &relays);
        });
    });

    let ui_weak = ui.as_weak();
    let relays_for_delete = Arc::clone(&relays);
    ui.on_delete_server(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let name = ui.get_server_name().to_string();
        let ui_weak = ui.as_weak();
        let relays = Arc::clone(&relays_for_delete);
        run_background(ui_weak, "Deleting server...", move |ui| {
            match delete_server(&name) {
                Ok(_) => append_log(&ui, &format!("[server:{name}] deleted\n")),
                Err(err) => {
                    append_log(&ui, &format!("[server:{name}] delete failed: {err}\n"));
                    set_status(&ui, "Server delete failed");
                    return;
                }
            }
            refresh_ui(&ui, &relays);
        });
    });
}

fn wire_relay_actions(ui: &AppWindow, relays: RelayProcesses) {
    let ui_weak = ui.as_weak();
    let relays_for_save = Arc::clone(&relays);
    ui.on_save_relay(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let config = RelayConfig::from_ui(&ui);
        let ui_weak = ui.as_weak();
        let relays = Arc::clone(&relays_for_save);
        run_background(ui_weak, "Saving relay...", move |ui| {
            match add_workspace(&config) {
                Ok(_) => append_log(&ui, &format!("[relay:{}] saved\n", config.name)),
                Err(err) => {
                    append_log(
                        &ui,
                        &format!("[relay:{}] save failed: {err}\n", config.name),
                    );
                    set_status(&ui, "Relay save failed");
                    return;
                }
            }
            refresh_ui(&ui, &relays);
        });
    });

    let ui_weak = ui.as_weak();
    let relays_for_start = Arc::clone(&relays);
    ui.on_start_relay(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let name = ui.get_relay_name().to_string();
        let ui_weak = ui.as_weak();
        let relays = Arc::clone(&relays_for_start);
        run_background(ui_weak, "Starting relay...", move |ui| {
            match start_relay(&name, &relays, &ui) {
                Ok(_) => append_log(&ui, &format!("[relay:{name}] start requested\n")),
                Err(err) => {
                    append_log(&ui, &format!("[relay:{name}] start failed: {err}\n"));
                    set_status(&ui, "Relay start failed");
                    return;
                }
            }
            refresh_ui(&ui, &relays);
        });
    });

    let ui_weak = ui.as_weak();
    let relays_for_stop = Arc::clone(&relays);
    ui.on_stop_relay(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let name = ui.get_relay_name().to_string();
        let ui_weak = ui.as_weak();
        let relays = Arc::clone(&relays_for_stop);
        run_background(ui_weak, "Stopping relay...", move |ui| {
            match stop_relay(&name, &relays) {
                Ok(_) => append_log(&ui, &format!("[relay:{name}] stop requested\n")),
                Err(err) => {
                    append_log(&ui, &format!("[relay:{name}] stop failed: {err}\n"));
                    set_status(&ui, "Relay stop failed");
                    return;
                }
            }
            refresh_ui(&ui, &relays);
        });
    });

    let ui_weak = ui.as_weak();
    let relays_for_delete = Arc::clone(&relays);
    ui.on_delete_relay(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let name = ui.get_relay_name().to_string();
        let ui_weak = ui.as_weak();
        let relays = Arc::clone(&relays_for_delete);
        run_background(ui_weak, "Deleting relay...", move |ui| {
            let _ = stop_relay(&name, &relays);
            match delete_workspace(&name) {
                Ok(_) => append_log(&ui, &format!("[relay:{name}] deleted\n")),
                Err(err) => {
                    append_log(&ui, &format!("[relay:{name}] delete failed: {err}\n"));
                    set_status(&ui, "Relay delete failed");
                    return;
                }
            }
            refresh_ui(&ui, &relays);
        });
    });
}

fn wire_image_actions(ui: &AppWindow, relays: RelayProcesses) {
    let ui_weak = ui.as_weak();
    ui.on_build_image(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let image_name = ui.get_image_name().to_string();
        let profile = ui.get_image_profile().to_string();
        let features = ui.get_image_features().to_string();
        let ui_weak = ui.as_weak();
        let relays = Arc::clone(&relays);
        run_background(ui_weak, "Building image...", move |ui| {
            match build_relay_image(&image_name, &profile, &features, &ui) {
                Ok(_) => append_log(&ui, &format!("[image-build] built {image_name}\n")),
                Err(err) => {
                    append_log(&ui, &format!("[image-build] failed: {err}\n"));
                    set_status(&ui, "Image build failed");
                    return;
                }
            }
            refresh_ui(&ui, &relays);
        });
    });
}

fn wire_path_picker(ui: &AppWindow) {
    let ui_weak = ui.as_weak();
    ui.on_browse_workspace_path(move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        let current = ui.get_workspace_path().to_string();
        let ui_weak = ui.as_weak();
        run_background(
            ui_weak,
            "Choosing folder...",
            move |ui| match choose_folder(&current) {
                Ok(Some(path)) => {
                    let path_for_log = path.clone();
                    let _ = ui.upgrade_in_event_loop(move |ui| {
                        ui.set_workspace_path(path.into());
                        ui.set_status_text("Ready".into());
                    });
                    append_log(&ui, &format!("[folder] selected {path_for_log}\n"));
                }
                Ok(None) => set_status(&ui, "Ready"),
                Err(err) => {
                    append_log(&ui, &format!("[folder] picker failed: {err}\n"));
                    set_status(&ui, "Folder picker failed");
                }
            },
        );
    });
}

fn wire_log_actions(ui: &AppWindow) {
    let ui_weak = ui.as_weak();
    ui.on_clear_logs(move || {
        let _ = ui_weak.upgrade_in_event_loop(|ui| ui.set_logs("".into()));
    });
}

fn wire_navigation(ui: &AppWindow) {
    let ui_weak = ui.as_weak();
    ui.on_show_relay_panel(move || {
        let _ = ui_weak.upgrade_in_event_loop(|ui| ui.set_active_panel(0));
    });

    let ui_weak = ui.as_weak();
    ui.on_show_server_panel(move || {
        let _ = ui_weak.upgrade_in_event_loop(|ui| ui.set_active_panel(1));
    });

    let ui_weak = ui.as_weak();
    ui.on_show_image_panel(move || {
        let _ = ui_weak.upgrade_in_event_loop(|ui| ui.set_active_panel(2));
    });
}

struct TrayState {
    _icon: TrayIcon,
    _event_pump: Option<Timer>,
}

fn schedule_tray_setup(ui: &AppWindow) -> Rc<RefCell<Option<TrayState>>> {
    let tray_state = Rc::new(RefCell::new(None));
    let tray_state_for_timer = Rc::clone(&tray_state);
    let ui_weak = ui.as_weak();
    Timer::single_shot(Duration::from_millis(0), move || {
        let Some(ui) = ui_weak.upgrade() else {
            return;
        };
        match setup_tray(&ui) {
            Ok(state) => *tray_state_for_timer.borrow_mut() = Some(state),
            Err(err) => append_log(&ui.as_weak(), &format!("[tray] unavailable: {err}\n")),
        }
    });
    tray_state
}

fn setup_tray(ui: &AppWindow) -> Result<TrayState, String> {
    let tray_menu = Menu::new();
    let show_item = MenuItem::with_id("show", "Show PawFlow Relay", true, None);
    let quit_item = MenuItem::with_id("quit", "Quit", true, None);
    tray_menu
        .append(&show_item)
        .map_err(|err| err.to_string())?;
    tray_menu
        .append(&quit_item)
        .map_err(|err| err.to_string())?;

    let ui_weak = ui.as_weak();
    ui.window().on_close_requested(move || {
        if let Some(ui) = ui_weak.upgrade() {
            let _ = ui.hide();
            ui.set_status_text("Running in system tray".into());
            append_log(
                &ui.as_weak(),
                "[tray] window hidden; use the tray icon to restore it\n",
            );
        }
        CloseRequestResponse::KeepWindowShown
    });

    let ui_weak = ui.as_weak();
    TrayIconEvent::set_event_handler(Some(move |event: TrayIconEvent| match event {
        TrayIconEvent::Click {
            button: MouseButton::Left,
            button_state: MouseButtonState::Up,
            ..
        }
        | TrayIconEvent::DoubleClick {
            button: MouseButton::Left,
            ..
        } => restore_window(&ui_weak),
        _ => {}
    }));

    let ui_weak = ui.as_weak();
    MenuEvent::set_event_handler(Some(move |event: MenuEvent| match event.id.as_ref() {
        "show" => restore_window(&ui_weak),
        "quit" => {
            let _ = slint::quit_event_loop();
        }
        _ => {}
    }));

    let icon = TrayIconBuilder::new()
        .with_menu(Box::new(tray_menu))
        .with_tooltip("PawFlow Relay Desktop")
        .with_icon(pawflow_tray_icon()?)
        .build()
        .map_err(|err| err.to_string())?;
    Ok(TrayState {
        _icon: icon,
        _event_pump: start_tray_event_pump(),
    })
}

#[cfg(target_os = "linux")]
fn start_tray_event_pump() -> Option<Timer> {
    if !gtk::is_initialized_main_thread() && gtk::init().is_err() {
        return None;
    }
    let timer = Timer::default();
    timer.start(TimerMode::Repeated, Duration::from_millis(100), || {
        while gtk::events_pending() {
            gtk::main_iteration_do(false);
        }
    });
    Some(timer)
}

#[cfg(not(target_os = "linux"))]
fn start_tray_event_pump() -> Option<Timer> {
    None
}

fn restore_window(ui: &slint::Weak<AppWindow>) {
    let ui = ui.clone();
    let _ = ui.upgrade_in_event_loop(|ui| {
        let _ = ui.show();
        ui.window().set_minimized(false);
        ui.set_status_text("Ready".into());
    });
}

fn pawflow_tray_icon() -> Result<Icon, String> {
    const SIZE: u32 = 32;
    let mut rgba = vec![0; (SIZE * SIZE * 4) as usize];
    for y in 0..SIZE {
        for x in 0..SIZE {
            let dx = x as i32 - 16;
            let dy = y as i32 - 16;
            let inside_badge = dx * dx + dy * dy <= 15 * 15;
            let p_stem = (9..=13).contains(&x) && (8..=24).contains(&y);
            let p_bowl = (13..=22).contains(&x) && (8..=16).contains(&y);
            let p_cutout = (15..=20).contains(&x) && (11..=14).contains(&y);
            let is_glyph = p_stem || (p_bowl && !p_cutout);
            let offset = ((y * SIZE + x) * 4) as usize;
            if inside_badge {
                rgba[offset] = if is_glyph { 255 } else { 31 };
                rgba[offset + 1] = if is_glyph { 255 } else { 115 };
                rgba[offset + 2] = if is_glyph { 255 } else { 216 };
                rgba[offset + 3] = 255;
            }
        }
    }
    Icon::from_rgba(rgba, SIZE, SIZE).map_err(|err| err.to_string())
}

#[derive(Clone, Debug)]
struct RelayConfig {
    name: String,
    server: String,
    path: String,
    mode: String,
    docker_image: String,
    allow_exec: bool,
    allow_remote_desktop: bool,
    allow_local: bool,
}

impl RelayConfig {
    fn from_ui(ui: &AppWindow) -> Self {
        Self {
            name: ui.get_relay_name().to_string(),
            server: ui.get_relay_server().to_string(),
            path: ui.get_workspace_path().to_string(),
            mode: ui.get_relay_mode().to_string(),
            docker_image: ui.get_docker_image().to_string(),
            allow_exec: ui.get_allow_exec(),
            allow_remote_desktop: ui.get_allow_remote_desktop(),
            allow_local: ui.get_allow_local_access(),
        }
    }
}

#[derive(Debug)]
struct DisplayState {
    summary: String,
    servers: String,
    relays: String,
    docker: String,
    catalog: String,
    server_row_name: String,
    server_row_meta: String,
    relay_row_name: String,
    relay_row_meta: String,
    server_name: String,
    server_url: String,
    relay_name: String,
    relay_server: String,
    relay_path: String,
    relay_mode: String,
    relay_docker_image: String,
}

fn run_background<F>(ui: slint::Weak<AppWindow>, status: &'static str, work: F)
where
    F: FnOnce(slint::Weak<AppWindow>) + Send + 'static,
{
    set_status(&ui, status);
    thread::spawn(move || work(ui));
}

fn refresh_ui(ui: &slint::Weak<AppWindow>, relays: &RelayProcesses) {
    match load_display_state(relays) {
        Ok(state) => {
            let _ = ui.upgrade_in_event_loop(move |ui| {
                ui.set_summary_text(state.summary.into());
                ui.set_server_list(state.servers.into());
                ui.set_relay_list(state.relays.into());
                ui.set_docker_list(state.docker.into());
                ui.set_catalog_text(state.catalog.into());
                ui.set_server_row_name(state.server_row_name.into());
                ui.set_server_row_meta(state.server_row_meta.into());
                ui.set_relay_row_name(state.relay_row_name.into());
                ui.set_relay_row_meta(state.relay_row_meta.into());
                if !state.server_name.is_empty() {
                    ui.set_server_name(state.server_name.into());
                }
                if !state.server_url.is_empty() {
                    ui.set_server_url(state.server_url.into());
                }
                if !state.relay_name.is_empty() {
                    ui.set_relay_name(state.relay_name.into());
                    ui.set_relay_server(state.relay_server.into());
                    ui.set_workspace_path(state.relay_path.into());
                    ui.set_relay_mode(state.relay_mode.into());
                    ui.set_docker_image(state.relay_docker_image.into());
                }
                ui.set_status_text("Ready".into());
            });
        }
        Err(err) => {
            append_log(ui, &format!("[refresh] {err}\n"));
            set_status(ui, "Refresh failed");
        }
    }
}

fn load_display_state(relays: &RelayProcesses) -> Result<DisplayState, String> {
    prune_exited_relays(relays);
    let state = get_relay_state()?;
    let running = running_names(relays);
    let servers = state
        .get("servers")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let workspaces = state
        .get("workspaces")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let docker_state = list_docker_images();
    let catalog = load_image_catalog();
    let server_summary = primary_server_summary(&servers);
    let relay_summary = primary_relay_summary(&workspaces, &running);

    Ok(DisplayState {
        summary: format!(
            "Servers: {} | Relays: {} | Running: {}",
            servers.len(),
            workspaces.len(),
            running.len()
        ),
        servers: format_servers(&servers),
        relays: format_workspaces(&workspaces, &running),
        docker: format_docker_state(&docker_state),
        catalog: format_catalog_state(&catalog),
        server_row_name: server_summary.row_name,
        server_row_meta: server_summary.row_meta,
        relay_row_name: relay_summary.row_name,
        relay_row_meta: relay_summary.row_meta,
        server_name: server_summary.name,
        server_url: server_summary.url,
        relay_name: relay_summary.name,
        relay_server: relay_summary.server,
        relay_path: relay_summary.path,
        relay_mode: relay_summary.mode,
        relay_docker_image: relay_summary.docker_image,
    })
}

fn get_relay_state() -> Result<Value, String> {
    run_python_json(
        "import json\nfrom pawflow_relay import manager\nprint(json.dumps({'servers': manager.list_servers(), 'workspaces': manager.list_workspaces()}))",
        &[],
    )
}

fn add_server(name: &str, url: &str, gateway_key: &str) -> Result<Value, String> {
    run_python_json(
        "import json, sys\nfrom pawflow_relay.manager import add_server\nprint(json.dumps(add_server(sys.argv[1], sys.argv[2], sys.argv[3])))",
        &[name, url, gateway_key],
    )
}

fn delete_server(name: &str) -> Result<Value, String> {
    run_python_json(
        "import json, sys\nfrom pawflow_relay.manager import delete_server\nprint(json.dumps(delete_server(sys.argv[1])))",
        &[name],
    )
}

fn add_workspace(config: &RelayConfig) -> Result<Value, String> {
    run_python_json(
        &[
            "import json, sys",
            "from pawflow_relay.manager import add_workspace",
            "allow_local = sys.argv[6].lower() == 'true'",
            "allow_exec = sys.argv[7].lower() == 'true'",
            "allow_remote_desktop = sys.argv[8].lower() == 'true'",
            "share = add_workspace(sys.argv[1], sys.argv[2], sys.argv[3], mode=sys.argv[4], docker_image=sys.argv[5], allow_local=allow_local, allow_exec=allow_exec, allow_remote_desktop=allow_remote_desktop)",
            "print(json.dumps(share))",
        ]
        .join("\n"),
        &[
            &config.name,
            &config.server,
            &config.path,
            &config.mode,
            &config.docker_image,
            bool_arg(config.allow_local),
            bool_arg(config.allow_exec),
            bool_arg(config.allow_remote_desktop),
        ],
    )
}

fn delete_workspace(name: &str) -> Result<Value, String> {
    run_python_json(
        "import json, sys\nfrom pawflow_relay.manager import delete_workspace\nprint(json.dumps(delete_workspace(sys.argv[1])))",
        &[name],
    )
}

fn login_server(name: &str, ui: &slint::Weak<AppWindow>) -> Result<(), String> {
    let mut cmd = base_python_module_command();
    cmd.arg("server").arg("login").arg(name);
    run_streamed_command(cmd, &format!("server:{name}"), ui).map(|_| ())
}

fn start_relay(
    name: &str,
    relays: &RelayProcesses,
    ui: &slint::Weak<AppWindow>,
) -> Result<(), String> {
    prune_exited_relays(relays);
    {
        let guard = relays
            .lock()
            .map_err(|_| "relay process lock poisoned".to_string())?;
        if guard.contains_key(name) {
            return Ok(());
        }
    }

    let mut cmd = base_python_module_command();
    cmd.arg("start")
        .arg(name)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|err| err.to_string())?;
    if let Some(stdout) = child.stdout.take() {
        let ui = ui.clone();
        let label = name.to_string();
        thread::spawn(move || stream_reader(ui, stdout, label));
    }
    if let Some(stderr) = child.stderr.take() {
        let ui = ui.clone();
        let label = name.to_string();
        thread::spawn(move || stream_reader(ui, stderr, label));
    }

    let mut guard = relays
        .lock()
        .map_err(|_| "relay process lock poisoned".to_string())?;
    guard.insert(name.to_string(), child);
    Ok(())
}

fn stop_relay(name: &str, relays: &RelayProcesses) -> Result<(), String> {
    let mut guard = relays
        .lock()
        .map_err(|_| "relay process lock poisoned".to_string())?;
    if let Some(mut child) = guard.remove(name) {
        child.kill().map_err(|err| err.to_string())?;
    }
    Ok(())
}

fn build_relay_image(
    image_name: &str,
    profile: &str,
    features_csv: &str,
    ui: &slint::Weak<AppWindow>,
) -> Result<(), String> {
    validate_docker_image_name(image_name)?;
    let build_root = env::temp_dir()
        .join("pawflow-relay-slint")
        .join("relay-image-builds");
    let out_dir = build_root.join(safe_image_build_name(image_name));
    let _ = fs::remove_dir_all(&out_dir);
    fs::create_dir_all(&build_root).map_err(|err| err.to_string())?;

    append_log(
        ui,
        &format!("[image-build] generating context for {image_name}\n"),
    );
    let mut generate = Command::new(python_command());
    apply_python_command_env(&mut generate);
    generate
        .arg(relay_image_generator_path()?)
        .arg("--catalog")
        .arg(relay_image_catalog_path()?)
        .arg("--profile")
        .arg(profile)
        .arg("--out")
        .arg(&out_dir)
        .arg("--image")
        .arg(image_name);
    for feature in parse_feature_list(features_csv) {
        generate.arg("--feature").arg(feature);
    }
    run_streamed_command(generate, "image-build", ui)?;

    append_log(ui, &format!("[image-build] docker build {image_name}\n"));
    run_docker_build(image_name, &out_dir, ui)?;
    append_log(ui, "[image-build] pruning Docker build cache\n");
    if let Err(err) = run_docker(&["builder", "prune", "-f"], "docker", ui) {
        append_log(ui, &format!("[docker] prune failed: {err}\n"));
    }
    let _ = fs::remove_dir_all(&out_dir);
    Ok(())
}

fn choose_folder(current: &str) -> Result<Option<String>, String> {
    if cfg!(windows) {
        choose_folder_windows(current)
    } else {
        choose_folder_unix(current)
    }
}

fn choose_folder_windows(current: &str) -> Result<Option<String>, String> {
    let script = r#"
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select PawFlow workspace folder'
$dialog.ShowNewFolderButton = $true
if ($args.Count -gt 0 -and $args[0] -and (Test-Path -LiteralPath $args[0])) { $dialog.SelectedPath = $args[0] }
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Write-Output $dialog.SelectedPath }
"#;
    let output = Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
            current,
        ])
        .output()
        .map_err(|err| err.to_string())?;
    folder_output(output)
}

fn choose_folder_unix(current: &str) -> Result<Option<String>, String> {
    let candidates: Vec<(&str, Vec<String>)> = vec![
        (
            "zenity",
            vec![
                "--file-selection".into(),
                "--directory".into(),
                "--filename".into(),
                current.into(),
            ],
        ),
        (
            "kdialog",
            vec!["--getexistingdirectory".into(), current.into()],
        ),
    ];
    for (program, args) in candidates {
        if command_exists(program) {
            let output = Command::new(program)
                .args(args)
                .output()
                .map_err(|err| err.to_string())?;
            return folder_output(output);
        }
    }
    Err("No folder picker found. Install zenity/kdialog or type the path manually.".into())
}

fn command_exists(program: &str) -> bool {
    let Some(paths) = env::var_os("PATH") else {
        return false;
    };
    env::split_paths(&paths).any(|dir| dir.join(program).exists())
}

fn folder_output(output: std::process::Output) -> Result<Option<String>, String> {
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if output.status.success() {
        if stdout.is_empty() {
            Ok(None)
        } else {
            Ok(Some(stdout))
        }
    } else if stdout.is_empty() && stderr.is_empty() {
        Ok(None)
    } else {
        Err(non_empty(
            stderr,
            stdout,
            format!("folder picker exited {}", output.status),
        ))
    }
}

fn run_python_json(source: &str, args: &[&str]) -> Result<Value, String> {
    let mut cmd = Command::new(python_command());
    apply_python_command_env(&mut cmd);
    cmd.arg("-c").arg(source);
    for arg in args {
        cmd.arg(arg);
    }
    let output = cmd.output().map_err(|err| err.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if !output.status.success() {
        return Err(non_empty(
            stderr,
            stdout,
            format!("Python exited {}", output.status),
        ));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|err| format!("Invalid JSON from Python: {err}\n{stdout}"))
}

fn base_python_module_command() -> Command {
    let mut cmd = Command::new(python_command());
    apply_python_command_env(&mut cmd);
    cmd.arg("-m").arg("pawflow_relay");
    cmd
}

fn apply_python_command_env(cmd: &mut Command) {
    cmd.current_dir(command_root());
    cmd.env("PAWFLOW_RELAY_RUNTIME_ROOT", runtime_root());
    cmd.env("PYTHONPATH", python_path());
}

fn python_command() -> String {
    env::var("PAWFLOW_RELAY_PYTHON")
        .or_else(|_| env::var("PYTHON"))
        .unwrap_or_else(|_| {
            if cfg!(windows) {
                "py".into()
            } else {
                "python3".into()
            }
        })
}

fn manifest_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn executable_root() -> Option<PathBuf> {
    env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))
}

fn repo_root() -> PathBuf {
    if let Ok(root) = env::var("PAWFLOW_REPO_ROOT") {
        return PathBuf::from(root);
    }
    let manifest = manifest_root();
    if let Some(parent) = manifest.parent() {
        if parent.join("pawflow_relay").exists() {
            return parent.to_path_buf();
        }
    }
    executable_root().unwrap_or(manifest)
}

fn runtime_root() -> PathBuf {
    if let Ok(root) = env::var("PAWFLOW_RELAY_RUNTIME_ROOT") {
        return PathBuf::from(root);
    }
    if let Some(root) = executable_root() {
        let runtime = root.join("runtime");
        if runtime.exists() {
            return runtime;
        }
    }
    let local_runtime = manifest_root().join("runtime");
    if local_runtime.exists() {
        return local_runtime;
    }
    let desktop_runtime = repo_root().join("pawflow-relay-desktop").join("runtime");
    if desktop_runtime.exists() {
        return desktop_runtime;
    }
    repo_root()
}

fn command_root() -> PathBuf {
    let repo = repo_root();
    if repo.join("pawflow_relay").exists() {
        repo
    } else {
        runtime_root()
    }
}

fn python_path() -> String {
    let delimiter = if cfg!(windows) { ";" } else { ":" };
    let mut roots = vec![runtime_root(), repo_root()];
    roots.dedup();
    let mut parts: Vec<String> = roots
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .collect();
    if let Ok(existing) = env::var("PYTHONPATH") {
        if !existing.is_empty() {
            parts.push(existing);
        }
    }
    parts.join(delimiter)
}

fn list_docker_images() -> Result<Value, String> {
    let out = run_docker_capture(&[
        "images",
        "--format",
        "{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}",
    ])?;
    let images: Vec<Value> = out
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.trim().split('\t').collect();
            if parts.len() < 4 || parts[0].is_empty() || parts[1].is_empty() {
                return None;
            }
            if parts[0] == "<none>" || parts[1] == "<none>" {
                return None;
            }
            Some(serde_json::json!({
                "name": format!("{}:{}", parts[0], parts[1]),
                "repository": parts[0],
                "tag": parts[1],
                "id": parts[2],
                "size": parts[3],
            }))
        })
        .collect();
    Ok(serde_json::json!({ "images": images, "error": "" }))
}

fn run_docker_capture(args: &[&str]) -> Result<String, String> {
    let output = Command::new(docker_command())
        .args(args)
        .output()
        .map_err(|err| err.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if output.status.success() {
        Ok(stdout)
    } else if cfg!(windows) && docker_connect_error(&stderr) {
        let output = Command::new("wsl.exe")
            .args(wsl_base_args())
            .arg("docker")
            .args(args)
            .output()
            .map_err(|err| err.to_string())?;
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        if output.status.success() {
            Ok(stdout)
        } else {
            Err(non_empty(
                stderr,
                stdout,
                format!("wsl docker exited {}", output.status),
            ))
        }
    } else {
        Err(non_empty(
            stderr,
            stdout,
            format!("docker exited {}", output.status),
        ))
    }
}

fn run_docker(args: &[&str], label: &str, ui: &slint::Weak<AppWindow>) -> Result<(), String> {
    let mut cmd = Command::new(docker_command());
    cmd.args(args);
    match run_streamed_command(cmd, label, ui) {
        Ok(out) => {
            if !out.trim().is_empty() {
                append_log(ui, &format!("[{label}] {}\n", out.trim_end()));
            }
            Ok(())
        }
        Err(err) if cfg!(windows) && docker_connect_error(&err) => {
            append_log(
                ui,
                "[docker] Windows Docker unavailable; trying WSL docker\n",
            );
            let mut cmd = Command::new("wsl.exe");
            cmd.args(wsl_base_args()).arg("docker").args(args);
            run_streamed_command(cmd, label, ui).map(|_| ())
        }
        Err(err) => Err(err),
    }
}

fn run_docker_build(
    image_name: &str,
    context_dir: &Path,
    ui: &slint::Weak<AppWindow>,
) -> Result<(), String> {
    let context = context_dir.to_string_lossy().to_string();
    let mut cmd = Command::new(docker_command());
    cmd.args(["build", "-t", image_name, &context]);
    match run_streamed_command(cmd, "image-build", ui) {
        Ok(_) => Ok(()),
        Err(err) if cfg!(windows) && docker_connect_error(&err) => {
            append_log(
                ui,
                "[docker] Windows Docker unavailable; trying WSL docker\n",
            );
            let wsl_context = wsl_path(context_dir)?;
            let mut cmd = Command::new("wsl.exe");
            cmd.args(wsl_base_args())
                .arg("docker")
                .args(["build", "-t", image_name, &wsl_context]);
            run_streamed_command(cmd, "image-build", ui).map(|_| ())
        }
        Err(err) => Err(err),
    }
}

fn run_streamed_command(
    mut cmd: Command,
    label: &str,
    ui: &slint::Weak<AppWindow>,
) -> Result<String, String> {
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|err| err.to_string())?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let output = Arc::new(Mutex::new(String::new()));

    let mut handles = Vec::new();
    if let Some(stdout) = stdout {
        let ui = ui.clone();
        let label = label.to_string();
        let output = Arc::clone(&output);
        handles.push(thread::spawn(move || {
            stream_and_capture(ui, stdout, label, output)
        }));
    }
    if let Some(stderr) = stderr {
        let ui = ui.clone();
        let label = label.to_string();
        let output = Arc::clone(&output);
        handles.push(thread::spawn(move || {
            stream_and_capture(ui, stderr, label, output)
        }));
    }

    let status = child.wait().map_err(|err| err.to_string())?;
    for handle in handles {
        let _ = handle.join();
    }
    let captured = output.lock().map(|guard| guard.clone()).unwrap_or_default();
    if status.success() {
        Ok(captured)
    } else {
        Err(non_empty(
            captured,
            String::new(),
            format!("command exited {status}"),
        ))
    }
}

fn stream_and_capture<R>(
    ui: slint::Weak<AppWindow>,
    reader: R,
    label: String,
    output: Arc<Mutex<String>>,
) where
    R: std::io::Read + Send + 'static,
{
    let reader = BufReader::new(reader);
    for line in reader.lines() {
        match line {
            Ok(line) => {
                if let Ok(mut guard) = output.lock() {
                    guard.push_str(&line);
                    guard.push('\n');
                }
                append_log(&ui, &format!("[{label}] {line}\n"));
            }
            Err(err) => {
                append_log(&ui, &format!("[{label}] stream error: {err}\n"));
                break;
            }
        }
    }
}

fn stream_reader<R>(ui: slint::Weak<AppWindow>, reader: R, label: String)
where
    R: std::io::Read + Send + 'static,
{
    let reader = BufReader::new(reader);
    for line in reader.lines() {
        match line {
            Ok(line) => append_log(&ui, &format!("[{label}] {line}\n")),
            Err(err) => {
                append_log(&ui, &format!("[{label}] stream error: {err}\n"));
                break;
            }
        }
    }
}

fn prune_exited_relays(relays: &RelayProcesses) {
    let Ok(mut guard) = relays.lock() else {
        return;
    };
    let mut exited = Vec::new();
    for (name, child) in guard.iter_mut() {
        match child.try_wait() {
            Ok(Some(_)) => exited.push(name.clone()),
            Ok(None) => {}
            Err(_) => exited.push(name.clone()),
        }
    }
    for name in exited {
        guard.remove(&name);
    }
}

fn running_names(relays: &RelayProcesses) -> Vec<String> {
    relays
        .lock()
        .map(|guard| guard.keys().cloned().collect())
        .unwrap_or_default()
}

fn load_image_catalog() -> Result<Value, String> {
    let text = fs::read_to_string(relay_image_catalog_path()?).map_err(|err| err.to_string())?;
    serde_json::from_str(&text).map_err(|err| err.to_string())
}

fn relay_image_catalog_path() -> Result<PathBuf, String> {
    first_existing_path(&[
        runtime_root()
            .join("config")
            .join("relay_image_catalog.json"),
        repo_root().join("config").join("relay_image_catalog.json"),
    ])
    .ok_or_else(|| "Relay image catalog not found".to_string())
}

fn relay_image_generator_path() -> Result<PathBuf, String> {
    first_existing_path(&[
        runtime_root()
            .join("scripts")
            .join("generate-relay-image.py"),
        repo_root().join("scripts").join("generate-relay-image.py"),
    ])
    .ok_or_else(|| "Relay image generator not found".to_string())
}

fn first_existing_path(candidates: &[PathBuf]) -> Option<PathBuf> {
    candidates.iter().find(|path| path.exists()).cloned()
}

struct ServerSummary {
    row_name: String,
    row_meta: String,
    name: String,
    url: String,
}

struct RelaySummary {
    row_name: String,
    row_meta: String,
    name: String,
    server: String,
    path: String,
    mode: String,
    docker_image: String,
}

fn primary_server_summary(servers: &[Value]) -> ServerSummary {
    let Some(server) = servers.first() else {
        return ServerSummary {
            row_name: "No server".into(),
            row_meta: "add profile".into(),
            name: String::new(),
            url: String::new(),
        };
    };
    let name = value_str(server, "name");
    let url = value_str(server, "url");
    let logged = truthy(server.get("session_token")) || truthy(server.get("logged_in"));
    ServerSummary {
        row_name: name.clone(),
        row_meta: if logged {
            "logged in".into()
        } else {
            "login needed".into()
        },
        name,
        url,
    }
}

fn primary_relay_summary(workspaces: &[Value], running: &[String]) -> RelaySummary {
    let Some(workspace) = workspaces.first() else {
        return RelaySummary {
            row_name: "No relay".into(),
            row_meta: "add relay".into(),
            name: String::new(),
            server: String::new(),
            path: String::new(),
            mode: "rw".into(),
            docker_image: String::new(),
        };
    };
    let name = value_str(workspace, "name");
    let server = value_str(workspace, "server");
    let path = value_str(workspace, "path");
    let mode = optional_value_str(workspace, "mode").unwrap_or_else(|| "rw".into());
    let docker_image = value_str(workspace, "docker_image");
    let status = if running.iter().any(|item| item == &name) {
        "running"
    } else {
        "stopped"
    };
    RelaySummary {
        row_name: name.clone(),
        row_meta: status.into(),
        name,
        server,
        path,
        mode,
        docker_image,
    }
}

fn format_servers(servers: &[Value]) -> String {
    if servers.is_empty() {
        return "No server configured".into();
    }
    servers
        .iter()
        .map(|server| {
            let name = value_str(server, "name");
            let url = value_str(server, "url");
            let user = value_str(server, "username");
            let logged = truthy(server.get("session_token")) || truthy(server.get("logged_in"));
            let status = if logged { "logged in" } else { "login needed" };
            let user_suffix = if user.is_empty() {
                String::new()
            } else {
                format!(" as {user}")
            };
            format!("{name}\n  {url}\n  {status}{user_suffix}")
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn format_workspaces(workspaces: &[Value], running: &[String]) -> String {
    if workspaces.is_empty() {
        return "No relay configured".into();
    }
    workspaces
        .iter()
        .map(|workspace| {
            let name = value_str(workspace, "name");
            let server = value_str(workspace, "server");
            let path = value_str(workspace, "path");
            let mode = optional_value_str(workspace, "mode").unwrap_or_else(|| "rw".into());
            let status = if running.iter().any(|item| item == &name) {
                "running"
            } else {
                "stopped"
            };
            format!("{name} ({status})\n  {server} / {mode}\n  {path}")
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn format_docker_state(state: &Result<Value, String>) -> String {
    match state {
        Ok(value) => {
            let images = value
                .get("images")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if images.is_empty() {
                return "No local Docker images found".into();
            }
            images
                .iter()
                .take(12)
                .map(|image| {
                    let name = value_str(image, "name");
                    let size = value_str(image, "size");
                    if size.is_empty() {
                        name
                    } else {
                        format!("{name} ({size})")
                    }
                })
                .collect::<Vec<_>>()
                .join("\n")
        }
        Err(err) => format!("Docker unavailable:\n{err}"),
    }
}

fn format_catalog_state(state: &Result<Value, String>) -> String {
    match state {
        Ok(value) => {
            let features = value
                .get("features")
                .and_then(Value::as_object)
                .map(|map| map.len())
                .unwrap_or(0);
            let profiles = value
                .get("profiles")
                .and_then(Value::as_object)
                .map(|map| map.len())
                .unwrap_or(0);
            format!("Catalog loaded: {profiles} presets, {features} features. Use comma-separated feature ids for custom builds.")
        }
        Err(err) => format!("Catalog unavailable: {err}"),
    }
}

fn value_str(value: &Value, key: &str) -> String {
    optional_value_str(value, key).unwrap_or_default()
}

fn optional_value_str(value: &Value, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(ToString::to_string)
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(flag)) => *flag,
        Some(Value::String(text)) => !text.is_empty(),
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0) != 0,
        _ => false,
    }
}

fn parse_feature_list(features_csv: &str) -> Vec<String> {
    features_csv
        .split(',')
        .map(str::trim)
        .filter(|feature| !feature.is_empty())
        .map(ToString::to_string)
        .collect()
}

fn validate_docker_image_name(image_name: &str) -> Result<(), String> {
    let valid = !image_name.is_empty()
        && image_name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '/' | '-' | ':'))
        && image_name
            .chars()
            .next()
            .map(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit())
            .unwrap_or(false);
    if valid {
        Ok(())
    } else {
        Err("Docker image name must look like pawflow-relay-custom:latest".into())
    }
}

fn safe_image_build_name(image_name: &str) -> String {
    let sanitized: String = image_name
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '-') {
                ch
            } else {
                '-'
            }
        })
        .collect();
    let trimmed = sanitized.trim_matches('-');
    if trimmed.is_empty() {
        "relay-image".into()
    } else {
        trimmed.into()
    }
}

fn docker_command() -> String {
    env::var("PAWFLOW_RELAY_DOCKER").unwrap_or_else(|_| "docker".into())
}

fn wsl_base_args() -> Vec<String> {
    match env::var("PAWFLOW_RELAY_WSL_DISTRO") {
        Ok(distro) if !distro.is_empty() => vec!["-d".into(), distro, "--".into()],
        _ => vec!["--".into()],
    }
}

fn wsl_path(path: &Path) -> Result<String, String> {
    let output = Command::new("wsl.exe")
        .args(wsl_base_args())
        .arg("wslpath")
        .arg("-a")
        .arg(path)
        .output()
        .map_err(|err| err.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if output.status.success() {
        Ok(stdout)
    } else {
        Err(non_empty(
            stderr,
            stdout,
            format!("wslpath exited {}", output.status),
        ))
    }
}

fn docker_connect_error(message: &str) -> bool {
    let text = message.to_ascii_lowercase();
    text.contains("dockerdesktoplinuxengine")
        || text.contains("npipe:")
        || text.contains("pipe/docker_engine")
        || text.contains("cannot connect to the docker daemon")
        || text.contains("failed to connect to the docker api")
        || text.contains("the system cannot find the file specified")
        || text.contains("enoent")
}

fn bool_arg(value: bool) -> &'static str {
    if value {
        "true"
    } else {
        "false"
    }
}

fn non_empty(first: String, second: String, fallback: String) -> String {
    let first = first.trim();
    if !first.is_empty() {
        return first.to_string();
    }
    let second = second.trim();
    if !second.is_empty() {
        return second.to_string();
    }
    fallback
}

fn set_status(ui: &slint::Weak<AppWindow>, status: &str) {
    let status = status.to_string();
    let _ = ui.upgrade_in_event_loop(move |ui| ui.set_status_text(status.into()));
}

fn append_log(ui: &slint::Weak<AppWindow>, text: &str) {
    let text = text.to_string();
    let _ = ui.upgrade_in_event_loop(move |ui| {
        let mut logs = ui.get_logs().to_string();
        logs.push_str(&text);
        if logs.len() > 80_000 {
            let keep_from = logs.len().saturating_sub(60_000);
            logs = logs[keep_from..].to_string();
        }
        ui.set_logs(logs.into());
    });
}
