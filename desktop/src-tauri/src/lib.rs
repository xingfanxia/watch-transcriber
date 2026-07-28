//! Thin desktop shell for the watch-transcriber archive.
//!
//! Serves the pipeline-generated archive (data/index.html + audio) over a
//! loopback axum server — ServeDir gives HTTP Range support, so audio seek
//! works — and opens a webview onto it. The viewer itself stays a single
//! Python-generated artifact; this shell adds no second copy of that logic.
//! Loopback note: the server binds 127.0.0.1 on a random port with no auth,
//! so the archive is readable by local processes while the app runs.

use std::path::{Path, PathBuf};

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::Html;
use axum::routing::{get, post};
use axum::Json;
use serde_json::Value;
use tauri::{WebviewUrl, WebviewWindowBuilder};
use tower_http::services::ServeDir;

/// Merge speaker-tag updates into data/manifest.json and persist them to the
/// private notes repo. The archive's user-authored state lives ONLY in the
/// manifest (Python's manifest delivery preserves the `speakers` field on
/// reprocess); this is a read-modify-write of that one field, atomic via
/// tmp + rename. Concurrent pipeline writes are last-writer-wins on the whole
/// file — acceptable for a single-user archive with second-scale windows.
async fn save_speakers(
    State(dir): State<PathBuf>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let err500 = |e: String| (StatusCode::INTERNAL_SERVER_ERROR, e);
    let path = dir.join("manifest.json");
    let raw = std::fs::read_to_string(&path).map_err(|e| err500(e.to_string()))?;
    let mut manifest: Value = serde_json::from_str(&raw).map_err(|e| err500(e.to_string()))?;

    let updates = body
        .get("updates")
        .and_then(Value::as_array)
        .ok_or((StatusCode::BAD_REQUEST, "missing updates[]".into()))?;
    let mut summary: Vec<String> = Vec::new();
    for u in updates {
        let (Some(key), Some(speakers)) = (
            u.get("key").and_then(Value::as_str),
            u.get("speakers").and_then(Value::as_object),
        ) else {
            continue;
        };
        let Some(entry) = manifest.get_mut(key).and_then(Value::as_object_mut) else {
            continue;
        };
        let map = entry
            .entry("speakers")
            .or_insert_with(|| Value::Object(Default::default()));
        let Some(map) = map.as_object_mut() else { continue };
        for (slot, name) in speakers {
            let name = name.as_str().unwrap_or("").trim();
            if name.is_empty() {
                map.remove(slot);
            } else {
                map.insert(slot.clone(), Value::String(name.to_string()));
                if !summary.contains(&name.to_string()) {
                    summary.push(name.to_string());
                }
            }
        }
        if map.is_empty() {
            entry.remove("speakers");
        }
    }

    let tmp = path.with_extension("json.tmp");
    let pretty = serde_json::to_string_pretty(&manifest).map_err(|e| err500(e.to_string()))?;
    std::fs::write(&tmp, pretty + "\n").map_err(|e| err500(e.to_string()))?;
    std::fs::rename(&tmp, &path).map_err(|e| err500(e.to_string()))?;

    let keys: Vec<String> = updates
        .iter()
        .filter_map(|u| u.get("key").and_then(Value::as_str).map(String::from))
        .collect();
    let label = if summary.is_empty() { "cleared".into() } else { summary.join(", ") };
    let msg = format!("speakers: {} ({} entries)", label, updates.len());
    let repo = dir.clone();
    std::thread::spawn(move || {
        // Map the tags into the transcript .md files (also rebuilds the
        // viewer), then persist everything to the private notes repo.
        if let Some(root) = repo.parent() {
            let _ = std::process::Command::new("python3")
                .arg("scripts/ops/apply_speakers.py")
                .args(&keys)
                .current_dir(root)
                .output();
        }
        git_commit_push(&repo, &msg);
    });
    Ok(Json(serde_json::json!({ "ok": true })))
}

/// Attach a markdown note to a recording: writes
/// data/<date>/<HHMMSS>-attachments/<name>.md, records it in the manifest
/// entry's `attachments` list, rebuilds the viewer, pushes the notes repo.
async fn save_attachment(
    State(dir): State<PathBuf>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let err500 = |e: String| (StatusCode::INTERNAL_SERVER_ERROR, e);
    let bad = |m: &str| (StatusCode::BAD_REQUEST, m.to_string());
    let key = body.get("key").and_then(Value::as_str).ok_or(bad("missing key"))?;
    let content = body.get("content").and_then(Value::as_str).ok_or(bad("missing content"))?;
    let raw_name = body.get("name").and_then(Value::as_str).unwrap_or("附注");

    // Whitelist-sanitize the filename (mirrors deliveries.safe_filename).
    let mut name: String = raw_name
        .chars()
        .filter(|c| c.is_alphanumeric() || " ()._-".contains(*c) || ('一'..='鿿').contains(c))
        .collect();
    name = name.trim().trim_matches('.').to_string();
    if name.is_empty() {
        name = "附注".into();
    }
    let name = name.strip_suffix(".md").unwrap_or(&name).to_string();

    let (date, hhmmss) = key.split_once(' ').ok_or(bad("bad key"))?;
    let att_dir = dir.join(date).join(format!("{hhmmss}-attachments"));
    std::fs::create_dir_all(&att_dir).map_err(|e| err500(e.to_string()))?;
    let mut path = att_dir.join(format!("{name}.md"));
    let mut n = 2;
    while path.exists() {
        path = att_dir.join(format!("{name}-{n}.md"));
        n += 1;
    }
    std::fs::write(&path, content).map_err(|e| err500(e.to_string()))?;
    let rel = format!(
        "{date}/{hhmmss}-attachments/{}",
        path.file_name().unwrap().to_string_lossy()
    );

    let manifest_path = dir.join("manifest.json");
    let raw = std::fs::read_to_string(&manifest_path).map_err(|e| err500(e.to_string()))?;
    let mut manifest: Value = serde_json::from_str(&raw).map_err(|e| err500(e.to_string()))?;
    let entry = manifest
        .get_mut(key)
        .and_then(Value::as_object_mut)
        .ok_or(bad("unknown key"))?;
    let list = entry
        .entry("attachments")
        .or_insert_with(|| Value::Array(vec![]));
    if let Some(list) = list.as_array_mut() {
        list.push(Value::String(rel.clone()));
    }
    let tmp = manifest_path.with_extension("json.tmp");
    let pretty = serde_json::to_string_pretty(&manifest).map_err(|e| err500(e.to_string()))?;
    std::fs::write(&tmp, pretty + "\n").map_err(|e| err500(e.to_string()))?;
    std::fs::rename(&tmp, &manifest_path).map_err(|e| err500(e.to_string()))?;

    let repo = dir.clone();
    let msg = format!("attach: {rel}");
    std::thread::spawn(move || {
        if let Some(root) = repo.parent() {
            let _ = std::process::Command::new("python3")
                .args(["-m", "deliveries.viewer"])
                .current_dir(root)
                .output();
        }
        git_commit_push(&repo, &msg);
    });
    Ok(Json(serde_json::json!({ "ok": true, "rel": rel })))
}

/// Persist a custom speaker color to data/speakers.json (source for the
/// viewer's spkColor override), then rebuild the viewer + push the repo.
async fn save_speaker_color(
    State(dir): State<PathBuf>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let err500 = |e: String| (StatusCode::INTERNAL_SERVER_ERROR, e);
    let bad = |m: &str| (StatusCode::BAD_REQUEST, m.to_string());
    let name = body.get("name").and_then(Value::as_str).ok_or(bad("missing name"))?;
    let color = body.get("color").and_then(Value::as_str).ok_or(bad("missing color"))?;
    if name.is_empty() || name.len() > 60 || name.contains('\n') {
        return Err(bad("bad name"));
    }
    let hex_ok = color.len() == 7
        && color.starts_with('#')
        && color[1..].chars().all(|c| c.is_ascii_hexdigit());
    if !hex_ok {
        return Err(bad("color must be #rrggbb"));
    }

    let path = dir.join("speakers.json");
    let mut colors: Value = match std::fs::read_to_string(&path) {
        Ok(raw) => serde_json::from_str(&raw).map_err(|e| err500(e.to_string()))?,
        Err(_) => serde_json::json!({}),
    };
    colors
        .as_object_mut()
        .ok_or_else(|| err500("speakers.json is not an object".into()))?
        .insert(name.to_string(), Value::String(color.to_string()));
    let tmp = path.with_extension("json.tmp");
    let pretty = serde_json::to_string_pretty(&colors).map_err(|e| err500(e.to_string()))?;
    std::fs::write(&tmp, pretty + "\n").map_err(|e| err500(e.to_string()))?;
    std::fs::rename(&tmp, &path).map_err(|e| err500(e.to_string()))?;

    let repo = dir.clone();
    let msg = format!("speakers: color {name} -> {color}");
    std::thread::spawn(move || {
        if let Some(root) = repo.parent() {
            let _ = std::process::Command::new("python3")
                .args(["-m", "deliveries.viewer"])
                .current_dir(root)
                .output();
        }
        git_commit_push(&repo, &msg);
    });
    Ok(Json(serde_json::json!({ "ok": true })))
}

/// Delete a recording — delegates to scripts/ops/delete_recording.py, the
/// single audited deletion path (files + manifest + views + rollups + R2 +
/// git commit/push). Runs synchronously so the UI gets a definitive answer.
async fn delete_recording(
    State(dir): State<PathBuf>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let key = body
        .get("key")
        .and_then(Value::as_str)
        .ok_or((StatusCode::BAD_REQUEST, "missing key".to_string()))?
        .to_string();
    let root = dir
        .parent()
        .ok_or((StatusCode::INTERNAL_SERVER_ERROR, "no repo root".to_string()))?
        .to_path_buf();
    let out = tokio::task::spawn_blocking(move || {
        std::process::Command::new("python3")
            .args(["scripts/ops/delete_recording.py", &key])
            .current_dir(root)
            .output()
    })
    .await
    .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
    .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let stdout = String::from_utf8_lossy(&out.stdout);
    let parsed: Value = serde_json::from_str(stdout.trim().lines().last().unwrap_or("{}"))
        .unwrap_or(serde_json::json!({"ok": false, "error": "unparseable script output"}));
    if !out.status.success() || parsed.get("ok") != Some(&Value::Bool(true)) {
        let detail = parsed
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or(&String::from_utf8_lossy(&out.stderr))
            .to_string();
        return Err((StatusCode::INTERNAL_SERVER_ERROR, detail));
    }
    Ok(Json(parsed))
}

/// Best-effort persistence to the private notes repo; offline is fine — the
/// next pipeline archive_git push carries the commit along.
fn git_commit_push(repo: &Path, msg: &str) {
    let git = |args: &[&str]| {
        std::process::Command::new("git")
            .arg("-C")
            .arg(repo)
            .args(args)
            .output()
    };
    let _ = git(&["add", "-A"]);
    if let Ok(s) = git(&["status", "--porcelain"]) {
        if s.stdout.is_empty() {
            return;
        }
    }
    let _ = git(&["commit", "-m", msg]);
    let _ = git(&["push"]);
}

fn data_dir() -> PathBuf {
    if let Ok(p) = std::env::var("WATCH_TRANSCRIBER_DATA") {
        return PathBuf::from(p);
    }
    // Walk up from the executable looking for the repo's data/ — covers the
    // debug/release bundle launched from inside any clone, wherever it lives.
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.as_path();
        while let Some(parent) = cur.parent() {
            let candidate = parent.join("data");
            if candidate.join("manifest.json").exists() || parent.join("transcribe.py").exists() {
                return candidate;
            }
            cur = parent;
        }
    }
    PathBuf::from(std::env::var("HOME").expect("HOME not set"))
        .join("projects/side-projects/watch-transcriber/data")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let dir = data_dir();
            let ready = dir.join("index.html").exists();

            let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
            let port = listener.local_addr()?.port();
            std::thread::spawn(move || {
                let rt = tokio::runtime::Runtime::new().expect("tokio runtime");
                rt.block_on(async move {
                    listener.set_nonblocking(true).expect("nonblocking listener");
                    let listener = tokio::net::TcpListener::from_std(listener)
                        .expect("tokio listener");
                    // /bootstrap is the fresh-machine landing page: it polls
                    // /index.html and redirects once restore_archive.py ran.
                    let router: axum::Router = axum::Router::new()
                        .route("/bootstrap", get(|| async {
                            Html(include_str!("bootstrap.html"))
                        }))
                        .route("/api/speakers", post(save_speakers))
                        .route("/api/attachments", post(save_attachment))
                        .route("/api/delete", post(delete_recording))
                        .route("/api/speaker-colors", post(save_speaker_color))
                        .fallback_service(ServeDir::new(dir.clone()))
                        .with_state(dir);
                    axum::serve(listener, router).await.expect("archive server");
                });
            });

            let page = if ready { "index.html" } else { "bootstrap" };
            let url = format!("http://127.0.0.1:{port}/{page}");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse()?))
                .title("AX语音Vault")
                .inner_size(1360.0, 900.0)
                .min_inner_size(800.0, 600.0)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
