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

    let msg = format!("speakers: {} ({} entries)", summary.join(", "), updates.len());
    let repo = dir.clone();
    std::thread::spawn(move || git_commit_push(&repo, &msg));
    Ok(Json(serde_json::json!({ "ok": true })))
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
                        .fallback_service(ServeDir::new(dir.clone()))
                        .with_state(dir);
                    axum::serve(listener, router).await.expect("archive server");
                });
            });

            let page = if ready { "index.html" } else { "bootstrap" };
            let url = format!("http://127.0.0.1:{port}/{page}");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse()?))
                .title("语音档案")
                .inner_size(1360.0, 900.0)
                .min_inner_size(800.0, 600.0)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
