//! Mobile sync core: pull the private notes repo as a GitHub tarball into the
//! app-sandbox data dir, stream/pin audio from R2, and expose the /api/sync/*
//! surface the setup page and viewer talk to.
//!
//! State machine (surfaced via GET /api/sync/status):
//!   no-tokens -> syncing -> ok | error | offline | unauthorized
//! Errors keep stale data usable; `last_sync` persists across launches in
//! sync_state.json (non-secret). Tokens live only in secrets.rs.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock};

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use tower::ServiceExt;
use tower_http::services::ServeDir;

use crate::{r2, secrets};

pub const DEFAULT_REPO: &str = "xingfanxia/watch-transcriber-data";
pub const DEFAULT_BUCKET: &str = "watch-transcriber-audio";

#[derive(Clone)]
pub struct GhCfg {
    pub pat: String,
    pub repo: String,
}

pub struct SyncCtx {
    pub data: PathBuf,
    pub base: PathBuf,
    pub cache: PathBuf,
    pub gh: RwLock<Option<GhCfg>>,
    pub r2: RwLock<Option<r2::R2Cfg>>,
    pub state: RwLock<String>,
    pub error: RwLock<Option<String>>,
    pub last_sync: RwLock<Option<String>>,
    pub syncing: AtomicBool,
    pub inflight: Arc<Mutex<HashSet<String>>>,
}

impl SyncCtx {
    /// Build the context and hydrate tokens + persisted state.
    pub fn new(data: PathBuf) -> Arc<Self> {
        if cfg!(mobile) {
            secrets::seed_from_build_env();
        }
        let base = data.parent().unwrap_or(Path::new(".")).to_path_buf();
        let cache = base.join("audio-cache");
        let ctx = SyncCtx {
            data,
            cache,
            gh: RwLock::new(None),
            r2: RwLock::new(None),
            state: RwLock::new("no-tokens".into()),
            error: RwLock::new(None),
            last_sync: RwLock::new(None),
            syncing: AtomicBool::new(false),
            inflight: Arc::new(Mutex::new(HashSet::new())),
            base,
        };
        let (repo, bucket) = ctx.config();
        if let Some(t) = secrets::load() {
            *ctx.gh.write().unwrap() = Some(GhCfg { pat: t.github_pat.clone(), repo });
            *ctx.r2.write().unwrap() = Some(r2::R2Cfg {
                account_id: t.r2_account_id,
                access_key_id: t.r2_access_key_id,
                secret_access_key: t.r2_secret_access_key,
                bucket,
            });
            *ctx.state.write().unwrap() = "idle".into();
        }
        if let Ok(raw) = std::fs::read_to_string(ctx.base.join("sync_state.json")) {
            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                *ctx.last_sync.write().unwrap() =
                    v.get("last_sync").and_then(Value::as_str).map(String::from);
            }
        }
        Arc::new(ctx)
    }

    /// Non-secret settings (repo, bucket) from sync_config.json, with defaults.
    fn config(&self) -> (String, String) {
        let mut repo = DEFAULT_REPO.to_string();
        let mut bucket = DEFAULT_BUCKET.to_string();
        if let Ok(raw) = std::fs::read_to_string(self.base.join("sync_config.json")) {
            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                if let Some(r) = v.get("repo").and_then(Value::as_str) {
                    repo = r.into();
                }
                if let Some(b) = v.get("bucket").and_then(Value::as_str) {
                    bucket = b.into();
                }
            }
        }
        (repo, bucket)
    }

    fn set_state(&self, state: &str, error: Option<String>) {
        *self.state.write().unwrap() = state.into();
        *self.error.write().unwrap() = error;
    }

    fn recordings(&self) -> usize {
        std::fs::read_to_string(self.data.join("manifest.json"))
            .ok()
            .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
            .and_then(|v| v.as_object().map(|o| o.len()))
            .unwrap_or(0)
    }
}

/// Map a reqwest failure to a sync state name.
fn classify(e: &reqwest::Error) -> &'static str {
    if e.is_connect() || e.is_timeout() || e.is_request() {
        "offline"
    } else {
        "error"
    }
}

/// Full pull: tarball download -> overlay extract into data/. Safe to call
/// repeatedly; concurrent calls collapse into one.
pub async fn pull(ctx: Arc<SyncCtx>) {
    if ctx.syncing.swap(true, Ordering::SeqCst) {
        return;
    }
    let result = pull_inner(&ctx).await;
    match result {
        Ok(()) => {
            let now = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true);
            *ctx.last_sync.write().unwrap() = Some(now.clone());
            let _ = std::fs::write(
                ctx.base.join("sync_state.json"),
                json!({ "last_sync": now }).to_string(),
            );
            ctx.set_state("ok", None);
        }
        Err((state, msg)) => ctx.set_state(state, Some(msg)),
    }
    ctx.syncing.store(false, Ordering::SeqCst);
}

async fn pull_inner(ctx: &SyncCtx) -> Result<(), (&'static str, String)> {
    let Some(gh) = ctx.gh.read().unwrap().clone() else {
        return Err(("no-tokens", "尚未配置 token".into()));
    };
    ctx.set_state("syncing", None);
    let url = format!("https://api.github.com/repos/{}/tarball/HEAD", gh.repo);
    let resp = r2::http()
        .get(&url)
        .header(header::USER_AGENT, "EchoWall")
        .header(header::AUTHORIZATION, format!("Bearer {}", gh.pat))
        .send()
        .await
        .map_err(|e| (classify(&e), e.to_string()))?;
    match resp.status().as_u16() {
        200 => {}
        401 | 403 => return Err(("unauthorized", format!("GitHub {}", resp.status()))),
        s => return Err(("error", format!("GitHub tarball HTTP {s}"))),
    }
    let bytes = resp
        .bytes()
        .await
        .map_err(|e| (classify(&e), e.to_string()))?;
    let data = ctx.data.clone();
    tokio::task::spawn_blocking(move || extract_overlay(&bytes, &data))
        .await
        .map_err(|e| ("error", e.to_string()))?
        .map_err(|e| ("error", e))
}

/// Extract a GitHub tarball over the data dir: strip the `owner-repo-sha/`
/// prefix, refuse path escapes, skip VCS files. Overlay only — local pinned
/// audio and cache are never deleted by a pull.
pub fn extract_overlay(tar_gz: &[u8], dest: &Path) -> Result<(), String> {
    let gz = flate2::read::GzDecoder::new(tar_gz);
    let mut archive = tar::Archive::new(gz);
    std::fs::create_dir_all(dest).map_err(|e| e.to_string())?;
    for entry in archive.entries().map_err(|e| e.to_string())? {
        let mut entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path().map_err(|e| e.to_string())?.into_owned();
        let mut comps = path.components();
        comps.next(); // strip owner-repo-sha/
        let rel: PathBuf = comps.as_path().to_path_buf();
        if rel.as_os_str().is_empty() {
            continue;
        }
        let unsafe_part = rel.components().any(|c| {
            matches!(c, std::path::Component::ParentDir | std::path::Component::RootDir)
        });
        if unsafe_part || rel.starts_with(".git") {
            continue;
        }
        let target = dest.join(&rel);
        match entry.header().entry_type() {
            tar::EntryType::Directory => {
                std::fs::create_dir_all(&target).map_err(|e| e.to_string())?;
            }
            tar::EntryType::Regular => {
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
                }
                entry.unpack(&target).map_err(|e| e.to_string())?;
            }
            _ => {}
        }
    }
    Ok(())
}

// ---------------------------------------------------------------- API routes

pub async fn status(State(ctx): State<Arc<SyncCtx>>) -> Json<Value> {
    Json(json!({
        "mode": if cfg!(mobile) { "mobile" } else { "desktop" },
        "state": ctx.state.read().unwrap().clone(),
        "error": ctx.error.read().unwrap().clone(),
        "last_sync": ctx.last_sync.read().unwrap().clone(),
        "recordings": ctx.recordings(),
    }))
}

/// Validate + persist tokens. Per-field errors so the setup page can point at
/// the failing credential instead of a generic failure.
pub async fn save_tokens(
    State(ctx): State<Arc<SyncCtx>>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let field = |k: &str| body.get(k).and_then(Value::as_str).unwrap_or("").trim().to_string();
    let tokens = secrets::SyncTokens {
        github_pat: field("github_pat"),
        r2_account_id: field("r2_account_id"),
        r2_access_key_id: field("r2_access_key_id"),
        r2_secret_access_key: field("r2_secret_access_key"),
    };
    let repo = {
        let r = field("repo");
        if r.is_empty() { DEFAULT_REPO.into() } else { r }
    };
    let bucket = {
        let b = field("bucket");
        if b.is_empty() { DEFAULT_BUCKET.into() } else { b }
    };
    let fail = |field: &str, msg: String| {
        (StatusCode::BAD_REQUEST, Json(json!({ "ok": false, "field": field, "error": msg })))
    };

    // GitHub: can this PAT see the notes repo?
    let gh_resp = r2::http()
        .get(format!("https://api.github.com/repos/{repo}"))
        .header(header::USER_AGENT, "EchoWall")
        .header(header::AUTHORIZATION, format!("Bearer {}", tokens.github_pat))
        .send()
        .await
        .map_err(|e| fail("github", format!("网络错误: {e}")))?;
    match gh_resp.status().as_u16() {
        200 => {}
        401 => return Err(fail("github", "token 无效 (401)".into())),
        404 => return Err(fail("github", format!("repo 访问被拒 ({repo})"))),
        s => return Err(fail("github", format!("GitHub HTTP {s}"))),
    }

    // R2: signed HEAD on the bucket root.
    let r2cfg = r2::R2Cfg {
        account_id: tokens.r2_account_id.clone(),
        access_key_id: tokens.r2_access_key_id.clone(),
        secret_access_key: tokens.r2_secret_access_key.clone(),
        bucket: bucket.clone(),
    };
    let r2_resp = r2::request(&r2cfg, "HEAD", "", None)
        .await
        .map_err(|e| fail("r2", format!("网络错误: {e}")))?;
    if !r2_resp.status().is_success() {
        return Err(fail("r2", format!("R2 拒绝访问 ({})", r2_resp.status())));
    }

    secrets::save(&tokens).map_err(|e| fail("store", format!("安全存储失败: {e}")))?;
    let _ = std::fs::write(
        ctx.base.join("sync_config.json"),
        json!({ "repo": repo, "bucket": bucket }).to_string(),
    );
    *ctx.gh.write().unwrap() = Some(GhCfg { pat: tokens.github_pat.clone(), repo });
    *ctx.r2.write().unwrap() = Some(r2cfg);
    ctx.set_state("idle", None);
    Ok(Json(json!({ "ok": true })))
}

pub async fn refresh(State(ctx): State<Arc<SyncCtx>>) -> Json<Value> {
    tokio::spawn(pull(ctx.clone()));
    Json(json!({ "ok": true }))
}

/// Pinned = the audio file exists inside data/ (survives pulls; excluded from
/// LRU eviction, which only runs on audio-cache/).
pub async fn pins(State(ctx): State<Arc<SyncCtx>>) -> Json<Value> {
    let mut pins: Vec<String> = Vec::new();
    let mut walk = vec![ctx.data.clone()];
    while let Some(dir) = walk.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                walk.push(p);
            } else if p.extension().is_some_and(|x| x == "m4a") {
                if let Ok(rel) = p.strip_prefix(&ctx.data) {
                    pins.push(rel.to_string_lossy().into_owned());
                }
            }
        }
    }
    Json(json!({ "pins": pins }))
}

pub async fn set_pin(
    State(ctx): State<Arc<SyncCtx>>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let rel = body
        .get("rel")
        .and_then(Value::as_str)
        .ok_or((StatusCode::BAD_REQUEST, "missing rel".into()))?;
    let on = body.get("on").and_then(Value::as_bool).unwrap_or(true);
    if rel.contains("..") || !rel.ends_with(".m4a") {
        return Err((StatusCode::BAD_REQUEST, "bad rel".into()));
    }
    let target = ctx.data.join(rel);
    if on {
        // Cache hit = local move instead of a re-download.
        let cached = ctx.cache.join(rel);
        if cached.exists() {
            if let Some(p) = target.parent() {
                let _ = std::fs::create_dir_all(p);
            }
            std::fs::rename(&cached, &target)
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
        } else {
            let cfg = ctx.r2.read().unwrap().clone().ok_or((
                StatusCode::CONFLICT,
                "R2 未配置".to_string(),
            ))?;
            r2::download_to(&cfg, rel, &target)
                .await
                .map_err(|e| (StatusCode::BAD_GATEWAY, e))?;
        }
    } else {
        let _ = std::fs::remove_file(&target);
    }
    Ok(Json(json!({ "ok": true, "pinned": on })))
}

/// Fallback for every non-API path: local archive first (Range via ServeDir),
/// then audio cache, then a signed R2 streaming proxy that also warms the
/// cache in the background.
#[axum::debug_handler]
pub async fn serve_or_fetch(State(ctx): State<Arc<SyncCtx>>, req: Request) -> Response {
    // Decompose into owned parts so no !Sync body reference crosses an await.
    let (parts, _body) = req.into_parts();
    let (uri, headers) = (parts.uri, parts.headers);
    let path = uri.path().to_string();
    let range = headers
        .get(header::RANGE)
        .and_then(|v| v.to_str().ok())
        .map(String::from);
    let make_req = || {
        let mut r = Request::builder().uri(uri.clone()).body(Body::empty()).unwrap();
        *r.headers_mut() = headers.clone();
        r
    };

    let local = ServeDir::new(&ctx.data)
        .oneshot(make_req())
        .await
        .expect("ServeDir is infallible");
    if local.status() != StatusCode::NOT_FOUND {
        return local.into_response();
    }
    if !path.ends_with(".m4a") {
        return StatusCode::NOT_FOUND.into_response();
    }
    let key = percent_encoding::percent_decode_str(path.trim_start_matches('/'))
        .decode_utf8_lossy()
        .into_owned();

    let cached = ctx.cache.join(&key);
    if cached.exists() {
        r2::touch(&cached);
        return ServeDir::new(&ctx.cache)
            .oneshot(make_req())
            .await
            .expect("ServeDir is infallible")
            .into_response();
    }

    let Some(cfg) = ctx.r2.read().unwrap().clone() else {
        return StatusCode::NOT_FOUND.into_response();
    };
    match r2::request(&cfg, "GET", &key, range.as_deref()).await {
        Ok(upstream) => {
            let status = upstream.status();
            if !(status.is_success() || status.as_u16() == 206) {
                return (StatusCode::BAD_GATEWAY, format!("R2 {status}")).into_response();
            }
            r2::spawn_cache_fill(cfg, ctx.cache.clone(), key, ctx.inflight.clone());
            let mut builder = Response::builder().status(status.as_u16());
            for h in [
                header::CONTENT_TYPE,
                header::CONTENT_LENGTH,
                header::CONTENT_RANGE,
                header::ACCEPT_RANGES,
                header::ETAG,
            ] {
                if let Some(v) = upstream.headers().get(&h) {
                    builder = builder.header(h, v);
                }
            }
            use futures_util::TryStreamExt;
            builder
                .body(Body::from_stream(upstream.bytes_stream().map_err(std::io::Error::other)))
                .unwrap_or_else(|e| {
                    (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response()
                })
        }
        Err(e) => (StatusCode::BAD_GATEWAY, format!("R2 代理失败: {e}")).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a gzipped tarball in memory with the GitHub `owner-repo-sha/` prefix.
    fn fake_tarball(files: &[(&str, &str)]) -> Vec<u8> {
        let mut builder = tar::Builder::new(flate2::write::GzEncoder::new(
            Vec::new(),
            flate2::Compression::fast(),
        ));
        for (path, content) in files {
            let mut header = tar::Header::new_gnu();
            header.set_size(content.len() as u64);
            header.set_mode(0o644);
            header.set_cksum();
            builder
                .append_data(&mut header, format!("owner-repo-abc123/{path}"), content.as_bytes())
                .unwrap();
        }
        builder.into_inner().unwrap().finish().unwrap()
    }

    #[test]
    fn extract_strips_prefix_and_overlays() {
        let dir = std::env::temp_dir().join(format!("echowall-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // Pre-existing pinned audio must survive the pull.
        std::fs::create_dir_all(dir.join("2026-07-20")).unwrap();
        std::fs::write(dir.join("2026-07-20/213456-test.m4a"), b"audio").unwrap();

        let tarball = fake_tarball(&[
            ("manifest.json", "{\"2026-07-20 213456\": {}}"),
            ("2026-07-20/213456-test.md", "# note"),
            (".git/config", "vcs noise"),
        ]);
        extract_overlay(&tarball, &dir).unwrap();

        assert!(dir.join("manifest.json").exists());
        assert!(dir.join("2026-07-20/213456-test.md").exists());
        assert!(dir.join("2026-07-20/213456-test.m4a").exists(), "pin survived");
        assert!(!dir.join(".git").exists(), "vcs entries skipped");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
