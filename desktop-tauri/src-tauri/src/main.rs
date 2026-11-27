#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::env;
use std::process::Command;

#[tauri::command]
fn run_dt(args: Vec<String>) -> Result<String, String> {
    let binary = env::var("DAYTHREAD_BIN").unwrap_or_else(|_| "dt".to_string());
    let mut command = Command::new(&binary);
    if binary.contains("python") {
        command.args(["-m", "daythread"]);
    }
    command.args(args);
    let output = command.output().map_err(|error| error.to_string())?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![run_dt])
        .run(tauri::generate_context!())
        .expect("error while running Daythread");
}
