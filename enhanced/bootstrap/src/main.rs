use serde_json::{json, Value};
use std::collections::HashSet;
use std::env;
use std::ffi::OsStr;
use std::fs;
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const CATALOG: &str = include_str!("../../bootstrap_catalog.json");
const INIT_ID: &str = "foundry-bootstrap:initialize";
const LIST_ID: &str = "foundry-bootstrap:tools/list";
const READ_ACTIVITY: &str = "notifications/foundry/bootstrap-read";
const COLD_READS: &[&str] = &[
    "script_grep",
    "script_read",
    "script_search",
    "inspect_instance",
    "search_game_tree",
    "list_roblox_studios",
    "get_studio_state",
    "get_console_output",
];

fn official_command() -> Option<Command> {
    let launcher = PathBuf::from(env::var_os("LOCALAPPDATA")?)
        .join("Roblox")
        .join("mcp.bat");
    if let Ok(script) = fs::read_to_string(&launcher) {
        if let Some((first, command)) = script
            .trim_start_matches('\u{feff}')
            .lines()
            .next()
            .zip(script.lines().nth(1))
        {
            if first.trim().eq_ignore_ascii_case("@echo off") {
                if let Some((path, rest)) = command
                    .trim()
                    .strip_prefix("if exist \"")
                    .and_then(|line| line.split_once("\" ( \""))
                {
                    if let Some((invoked, _)) = rest.split_once("\" %* ) else (for /f ") {
                        let exe = Path::new(path);
                        let versions = launcher.parent().unwrap().join("Versions");
                        if invoked == path
                            && exe.file_name() == Some(OsStr::new("StudioMCP.exe"))
                            && exe.parent().is_some_and(|dir| {
                                dir.parent() == Some(&versions)
                                    && dir.file_name().is_some_and(|name| {
                                        name.to_string_lossy().starts_with("version-")
                                    })
                            })
                            && exe.is_file()
                        {
                            return Some(Command::new(exe));
                        }
                    }
                }
            }
        }
    }
    None
}

fn spawn(command: &mut Command) -> io::Result<Child> {
    command.stdin(Stdio::piped()).stdout(Stdio::piped()).spawn()
}

fn send(to: &mut impl Write, message: &Value) -> io::Result<()> {
    serde_json::to_writer(&mut *to, message)?;
    to.write_all(b"\n")?;
    to.flush()
}

fn list_tools(result: &mut Value, params: &Value, catalog: &Value) {
    let Some(tools) = result["tools"].as_array_mut() else {
        return;
    };
    for tool in tools.iter_mut() {
        let name = tool["name"].as_str().unwrap_or("").to_owned();
        if let Some(description) = catalog["native_descriptions"].get(&name) {
            tool["description"] = description.clone();
        }
        if tool.get("inputSchema").is_none() {
            tool["inputSchema"] = json!({"type":"object"});
        }
        let props = &mut tool["inputSchema"]["properties"];
        props["as_agent"] = json!({"type":"string"});
        for (key, field) in props.as_object_mut().unwrap() {
            let specific = catalog["native_fields"]
                .as_array()
                .unwrap()
                .iter()
                .find(|row| row[0] == name && row[1] == *key)
                .map(|row| &row[2]);
            let short = specific.or_else(|| catalog["shared_fields"].get(key));
            if let (Some(short), Some(fields)) = (short, field.as_object_mut()) {
                if fields.contains_key("description") {
                    if short == "" {
                        fields.remove("description");
                    } else {
                        fields.insert("description".to_string(), short.clone());
                    }
                }
            }
        }
    }
    if params["cursor"].as_str().unwrap_or("").is_empty() {
        for extension in catalog["extensions"].as_array().unwrap() {
            if !tools.iter().any(|tool| tool["name"] == extension["name"]) {
                tools.push(extension.clone());
            }
        }
    }
}

fn emit(output: &Mutex<BufWriter<io::Stdout>>, response: &Value) -> io::Result<()> {
    send(&mut *output.lock().unwrap(), response)
}

fn exchange(
    official: &mut BufWriter<ChildStdin>,
    replies: &mut impl BufRead,
    message: &Value,
    output: &Mutex<BufWriter<io::Stdout>>,
) -> io::Result<Value> {
    send(official, message)?;
    loop {
        let mut line = String::new();
        if replies.read_line(&mut line)? == 0 {
            return Err(io::Error::other("StudioMCP closed during discovery"));
        }
        let response: Value = serde_json::from_str(&line)?;
        if response.get("id") == message.get("id") && response.get("method").is_none() {
            return Ok(response);
        }
        emit(output, &response)?;
    }
}

fn process(
    initial: impl BufRead,
    official: &mut Child,
    backend: &mut Child,
    catalog: &Value,
) -> io::Result<()> {
    let mut official_input = BufWriter::new(official.stdin.take().unwrap());
    let mut official_output = BufReader::new(official.stdout.take().unwrap());
    let mut runtime = BufWriter::new(backend.stdin.take().unwrap());
    let runtime_output = BufReader::new(backend.stdout.take().unwrap());
    let output = Arc::new(Mutex::new(BufWriter::new(io::stdout())));
    let writer = output.clone();
    let relay = thread::spawn(move || -> io::Result<()> {
        for line in runtime_output.lines() {
            let line = line?;
            let response: Value = serde_json::from_str(&line)?;
            if response.get("method").is_none()
                && matches!(response["id"].as_str(), Some(INIT_ID | LIST_ID))
            {
                continue;
            }
            let mut writer = writer.lock().unwrap();
            writeln!(writer, "{line}")?;
            writer.flush()?;
        }
        Ok(())
    });

    let conversation = (|| -> io::Result<()> {
        let mut discovering = true;
        let mut native_reads = HashSet::new();
        for line in initial.lines() {
            let line = line?;
            let parsed = serde_json::from_str::<Value>(&line);
            if parsed.as_ref().is_ok_and(|message| {
                message["method"] == READ_ACTIVITY && message.get("id").is_none()
            }) {
                continue;
            }
            if discovering {
                if let Ok(message) = parsed {
                    let method = message["method"].as_str();
                    match method {
                        Some("initialize" | "tools/list") if message.get("id").is_some() => {
                            let mut replay = message.clone();
                            replay["id"] = json!(if method == Some("initialize") {
                                INIT_ID
                            } else {
                                LIST_ID
                            });
                            send(&mut runtime, &replay)?;
                            let mut response = exchange(
                                &mut official_input,
                                &mut official_output,
                                &message,
                                &output,
                            )?;
                            if let Some(result) = response.get_mut("result") {
                                if method == Some("initialize") {
                                    result["serverInfo"]["name"] = json!("Foundry MCP");
                                    result["serverInfo"]["version"] =
                                        json!(env!("CARGO_PKG_VERSION"));
                                    let instructions =
                                        result["instructions"].as_str().unwrap_or("");
                                    result["instructions"] = json!(format!(
                                        "{instructions}{}",
                                        catalog["instructions"].as_str().unwrap()
                                    ));
                                } else {
                                    if let Some(tools) = result["tools"].as_array() {
                                        for tool in tools {
                                            if let Some(name) = tool["name"].as_str() {
                                                if COLD_READS.contains(&name)
                                                    && tool["annotations"]["readOnlyHint"] == true
                                                {
                                                    native_reads.insert(name.to_owned());
                                                }
                                            }
                                        }
                                    }
                                    list_tools(result, &message["params"], catalog);
                                }
                            }
                            emit(&output, &response)?;
                            continue;
                        }
                        Some("notifications/initialized") => {
                            send(&mut runtime, &message)?;
                            send(&mut official_input, &message)?;
                            continue;
                        }
                        Some("tools/call")
                            if message.get("id").is_some()
                                && message["params"]["name"]
                                    .as_str()
                                    .is_some_and(|name| native_reads.contains(name))
                                && message["params"]["arguments"]
                                    .as_object()
                                    .is_some_and(|args| !args.contains_key("as_agent")) =>
                        {
                            let start = Instant::now();
                            let started = SystemTime::now()
                                .duration_since(UNIX_EPOCH)
                                .unwrap_or_default()
                                .as_secs_f64();
                            let response = exchange(
                                &mut official_input,
                                &mut official_output,
                                &message,
                                &output,
                            )?;
                            emit(&output, &response)?;
                            send(
                                &mut runtime,
                                &json!({
                                    "jsonrpc": "2.0",
                                "method": READ_ACTIVITY,
                                    "params": {
                                        "tool": message["params"]["name"],
                                        "arguments": message["params"]["arguments"],
                                        "places": if message["params"]["name"] == "list_roblox_studios" {
                                            response["result"].clone()
                                        } else { Value::Null },
                                        "started": started,
                                        "elapsed_ms": start.elapsed().as_secs_f64() * 1000.0,
                                        "success": response.get("error").is_none() && response["result"]["isError"] != true,
                                    },
                                }),
                            )?;
                            continue;
                        }
                        _ => {}
                    }
                }
                discovering = false;
                // The regular Foundry server now owns all calls and writes.
                let _ = official.kill();
            }
            writeln!(runtime, "{line}")?;
            runtime.flush()?;
        }
        Ok(())
    })();
    let _ = official.kill();
    if conversation.is_err() {
        let _ = backend.kill();
    }
    drop(runtime);
    let relayed = relay
        .join()
        .map_err(|_| io::Error::other("Foundry output relay failed"))?;
    conversation.and(relayed)
}

fn stable_version(value: &str) -> Option<[u32; 3]> {
    let values: Vec<_> = value
        .split('.')
        .map(|part| {
            if part.is_empty() || part.len() > 6 || !part.bytes().all(|c| c.is_ascii_digit()) {
                return None;
            }
            part.parse::<u32>().ok()
        })
        .collect::<Option<_>>()?;
    values.try_into().ok()
}

fn run() -> io::Result<()> {
    let args: Vec<_> = env::args_os().skip(1).collect();
    let executable = env::current_exe()?;
    let local_root = executable
        .parent()
        .and_then(Path::parent)
        .ok_or_else(|| io::Error::other("Foundry installation path is invalid"))?;
    let original_root = env::var_os("FOUNDRY_INSTALL_ROOT").map(PathBuf::from);
    let install_root = original_root.as_deref().unwrap_or(local_root);
    if original_root.is_none()
        && env::var_os("FOUNDRY_DISABLE_UPDATES").is_none()
        && !args
            .iter()
            .take_while(|arg| *arg != "--upstream")
            .any(|arg| arg == "--no-updates")
    {
        let updates = local_root.join(".foundry-updates");
        if let Ok(active) = fs::read_to_string(updates.join("active")) {
            let active = active.trim();
            if stable_version(active) > stable_version(env!("CARGO_PKG_VERSION")) {
                let updated = updates.join(active).join("enhanced/foundry-bootstrap.exe");
                if updated.is_file() {
                    let status = Command::new(updated)
                        .env("FOUNDRY_INSTALL_ROOT", install_root)
                        .args(&args)
                        .status()?;
                    std::process::exit(status.code().unwrap_or(1));
                }
            }
        }
    }

    let server = executable.with_file_name("server.py");
    if !server.is_file() {
        return Err(io::Error::other(
            "Place foundry-bootstrap alongside enhanced/server.py",
        ));
    }
    let python = env::var_os("FOUNDRY_PYTHON").unwrap_or_else(|| "python.exe".into());
    let mut python = Command::new(python);
    python
        .env("FOUNDRY_INSTALL_ROOT", install_root)
        .arg(server)
        .arg("--workspace")
        .arg(install_root)
        .args(&args);
    let official_command = official_command().filter(|_| {
        !args
            .iter()
            .any(|arg| arg == "--help" || arg == "-h" || arg == "--upstream")
    });
    let Some(mut command) = official_command else {
        let status = python.status()?;
        std::process::exit(status.code().unwrap_or(1));
    };
    let catalog: Value = serde_json::from_str(CATALOG)?;
    let mut official = spawn(&mut command)?;
    let mut backend = match spawn(python.env("FOUNDRY_RUST_BACKEND", "1")) {
        Ok(child) => child,
        Err(error) => {
            let _ = official.kill();
            let _ = official.wait();
            return Err(error);
        }
    };
    let result = process(io::stdin().lock(), &mut official, &mut backend, &catalog);
    let _ = official.wait();
    let backend_status = backend.wait()?;
    result?;
    if !backend_status.success() {
        return Err(io::Error::other(format!(
            "Foundry runtime exited with {backend_status}"
        )));
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("Foundry bootstrap: {error}");
        std::process::exit(1);
    }
}
