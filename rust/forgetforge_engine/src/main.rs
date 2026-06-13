use std::io::{self, Read};

use forgetforge_engine_native::run_command;
use serde_json::Value;

fn main() {
    if let Err(err) = run() {
        let out = serde_json::json!({"ok": false, "error": err});
        println!("{out}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        return Err("usage: forgetforge-engine <score|tier>".into());
    }
    let payload = read_json()?;
    let result = run_command(&args[1], &payload)?;
    let out = serde_json::json!({"ok": true, "result": result});
    println!("{}", serde_json::to_string(&out).map_err(|e| e.to_string())?);
    Ok(())
}

fn read_json() -> Result<Value, String> {
    let mut raw = String::new();
    io::stdin().read_to_string(&mut raw).map_err(|e| e.to_string())?;
    if raw.trim().is_empty() {
        return Ok(Value::Object(serde_json::Map::new()));
    }
    serde_json::from_str(&raw).map_err(|e| e.to_string())
}
