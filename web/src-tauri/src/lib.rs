use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;
use tokio::time::sleep;

/// 后端实际端口，由 sidecar 写入端口文件后读取
struct BackendPort(Mutex<Option<u16>>);

#[tauri::command]
fn get_backend_port(port: tauri::State<BackendPort>) -> Option<u16> {
    *port.0.lock().unwrap()
}

#[tauri::command]
fn app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[tauri::command]
fn check_backend_health(port: tauri::State<BackendPort>) -> bool {
    let p = *port.0.lock().unwrap();
    match p {
        None => false,
        Some(port) => {
            let url = format!("http://127.0.0.1:{}/api/v1/hub/health", port);
            reqwest::blocking::get(&url)
                .map(|resp| resp.status().is_success())
                .unwrap_or(false)
        }
    }
}

#[tauri::command]
fn greet(name: &str) -> String {
    format!("你好, {}! 欢迎使用港 Harbour。", name)
}

/// 寻找 venv Python 解释器。多策略 fallback：
///   1. VIRTUAL_ENV 环境变量指向的 bin/python
///   2. 项目根目录下 backed/.venv/Scripts 或 backed/.venv/bin
///   3. uv 管理的标准 venv（项目 .venv）
fn _find_venv_python() -> PathBuf {
    // 策略 1: VIRTUAL_ENV 环境变量
    if let Ok(venv) = std::env::var("VIRTUAL_ENV") {
        let pb = PathBuf::from(&venv);
        let candidate = if cfg!(windows) {
            pb.join("Scripts").join("python.exe")
        } else {
            pb.join("bin").join("python3")
        };
        if candidate.is_file() {
            return candidate;
        }
        // fallback: python.exe 在 Windows 上也可能直接是 python
        let alt = pb.join("Scripts").join("python");
        if alt.is_file() {
            return alt;
        }
    }

    // 策略 2: 项目 backed/.venv
    let base = std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."));
    let candidates: Vec<PathBuf> = if cfg!(windows) {
        vec![
            base.join("..").join("..").join("backed").join(".venv").join("Scripts").join("python.exe"),
            base.join("backed").join(".venv").join("Scripts").join("python.exe"),
            // 项目根目录下的 .venv
            base.join("..").join("..").join(".venv").join("Scripts").join("python.exe"),
        ]
    } else {
        vec![
            base.join("../backed/.venv/bin/python3"),
            base.join("../backed/.venv/bin/python"),
            base.join("backed/.venv/bin/python3"),
            base.join("../.venv/bin/python3"),
        ]
    };

    for c in &candidates {
        let resolved = c.canonicalize().unwrap_or_else(|_| c.clone());
        if resolved.is_file() {
            return resolved;
        }
    }

    // 策略 3: 所有 fallback 失败，回退到系统 python（会报错但至少有错误提示）
    eprintln!("[harbour] 未找到 venv Python，回退到系统 python");
    if cfg!(windows) {
        PathBuf::from("python.exe")
    } else {
        PathBuf::from("python3")
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .manage(BackendPort(Mutex::new(None)))
        .setup(|app| {
            let app_handle = app.handle().clone();

            // 端口文件路径
            let port_file: PathBuf = if cfg!(debug_assertions) {
                // dev 模式: 项目 src-tauri 目录
                std::env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join(".tauri-port")
            } else {
                // 生产模式: app data 目录
                app_handle
                    .path()
                    .app_data_dir()
                    .unwrap_or_else(|_| {
                        PathBuf::from(std::env::var("APPDATA").unwrap_or_else(|_| ".".to_string()))
                            .join("harbour")
                    })
                    .join("tauri-port")
            };

            // 确保端口文件目录存在
            if let Some(parent) = port_file.parent() {
                let _ = std::fs::create_dir_all(parent);
            }

            // 清空旧端口文件
            let _ = std::fs::remove_file(&port_file);

            // sidecar 脚本路径
            let sidecar_path = if cfg!(debug_assertions) {
                std::env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join("..")
                    .join("..")
                    .join("backed")
                    .join("desktop_sidecar.py")
            } else {
                // 生产模式：PyInstaller 打包后的 exe
                app_handle
                    .path()
                    .resource_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join("binaries")
                    .join("harbour-backend-x86_64-pc-windows-msvc.exe")
            };

            // 数据目录
            let data_dir: PathBuf = if cfg!(debug_assertions) {
                std::env::current_dir()
                    .unwrap_or_else(|_| PathBuf::from("."))
                    .join("..")
                    .join("..")
                    .join("backed")
                    .join("data")
            } else {
                app_handle
                    .path()
                    .app_data_dir()
                    .unwrap_or_else(|_| {
                        PathBuf::from(std::env::var("APPDATA").unwrap_or_else(|_| ".".to_string()))
                            .join("harbour")
                    })
            };

            let port_file_clone = port_file.clone();

            // 启动 sidecar 子进程
            tauri::async_runtime::spawn(async move {
                use std::process::Command;

                let mut cmd = if cfg!(debug_assertions) {
                    // 使用项目 uv 虚拟环境的 Python，而非系统 Python
                    // 优先级：VIRTUAL_ENV 环境变量 > 项目 .venv > 自动探测
                    let venv_python = _find_venv_python();
                    let mut c = Command::new(&venv_python);
                    c.arg(&sidecar_path)
                        .arg("--port").arg("0")
                        .arg("--tauri-port-file").arg(&port_file_clone)
                        .arg("--data-dir").arg(&data_dir);
                    c
                } else {
                    let mut c = Command::new(&sidecar_path);
                    c.arg("--port").arg("0")
                        .arg("--tauri-port-file").arg(&port_file_clone)
                        .arg("--data-dir").arg(&data_dir);
                    c
                };

                match cmd.spawn() {
                    Ok(child) => {
                        eprintln!("[harbour] sidecar 已启动, PID: {:?}", child.id());
                    }
                    Err(e) => {
                        eprintln!("[harbour] 启动 sidecar 失败: {}", e);
                        return;
                    }
                }

                // 等待端口文件出现（最多 30 秒）
                for _ in 0..300 {
                    if let Ok(content) = std::fs::read_to_string(&port_file_clone) {
                        let trimmed = content.trim();
                        if let Ok(port) = trimmed.parse::<u16>() {
                            if port > 0 {
                                eprintln!("[harbour] 后端端口: {}", port);
                                // 更新状态
                                let state: tauri::State<BackendPort> = app_handle.state();
                                *state.0.lock().unwrap() = Some(port);

                                // 注入到 webview
                                if let Some(window) = app_handle.get_webview_window("main") {
                                    let script = format!(
                                        "window.__TAURI_PORT__ = {};\nwindow.__TAURI_APP_VERSION__ = '{}';\nconsole.log('[harbour] 桌面版就绪, 后端端口: {}');",
                                        port,
                                        env!("CARGO_PKG_VERSION"),
                                        port
                                    );
                                    let _ = window.eval(&script);
                                }
                                return;
                            }
                        }
                    }
                    sleep(Duration::from_millis(100)).await;
                }

                eprintln!("[harbour] 等待后端端口超时");
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_backend_port,
            app_version,
            check_backend_health,
            greet
        ])
        .run(tauri::generate_context!())
        .expect("Tauri 启动失败");
}
