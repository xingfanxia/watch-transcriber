//! R2 audio access: SigV4-signed GETs against the S3-compatible endpoint,
//! streaming Range passthrough for playback, and a size-capped LRU disk cache
//! filled in the background so replays go local.
//!
//! Object keys are the archive-relative audio paths (`YYYY-MM-DD/HHMMSS-<slug>.m4a`)
//! — the same contract deliveries/r2_backup.py uploads under.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};

pub const CACHE_CAP_BYTES: u64 = 500 * 1024 * 1024;

/// Shared HTTP client with bundled webpki roots. Rationale: reqwest's default
/// rustls path verifies TLS through rustls-platform-verifier, which on Android
/// needs JNI + a Gradle-side Kotlin component and panics when absent. We talk
/// to exactly two hosts (github.com, r2.cloudflarestorage.com) — Mozilla's
/// bundled roots cover both on every platform with zero platform glue.
pub fn http() -> &'static reqwest::Client {
    static CLIENT: std::sync::OnceLock<reqwest::Client> = std::sync::OnceLock::new();
    CLIENT.get_or_init(|| {
        let roots = rustls::RootCertStore {
            roots: webpki_roots::TLS_SERVER_ROOTS.to_vec(),
        };
        let tls = rustls::ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth();
        reqwest::Client::builder()
            .use_preconfigured_tls(tls)
            .build()
            .expect("reqwest client")
    })
}

#[derive(Clone)]
pub struct R2Cfg {
    pub account_id: String,
    pub access_key_id: String,
    pub secret_access_key: String,
    pub bucket: String,
}

fn hmac_sha256(key: &[u8], data: &[u8]) -> Vec<u8> {
    use hmac::digest::KeyInit;
    let mut mac = <Hmac<Sha256>>::new_from_slice(key).expect("hmac accepts any key len");
    mac.update(data);
    mac.finalize().into_bytes().to_vec()
}

fn sha256_hex(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

/// AWS SigV4 signing key derivation (kSecret -> kDate -> kRegion -> kService -> kSigning).
fn signing_key(secret: &str, date: &str, region: &str, service: &str) -> Vec<u8> {
    let k = hmac_sha256(format!("AWS4{secret}").as_bytes(), date.as_bytes());
    let k = hmac_sha256(&k, region.as_bytes());
    let k = hmac_sha256(&k, service.as_bytes());
    hmac_sha256(&k, b"aws4_request")
}

/// RFC 3986 encode a single path segment (S3 canonical URI keeps `/` separators).
fn uri_encode_segment(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Build a signed GET/HEAD request for `key` ("" = bucket root) on the R2
/// S3 endpoint. Returns (url, headers) ready for reqwest.
pub fn sign_get(
    cfg: &R2Cfg,
    method: &str,
    key: &str,
    range: Option<&str>,
    now: chrono::DateTime<chrono::Utc>,
) -> (String, Vec<(String, String)>) {
    let host = format!("{}.r2.cloudflarestorage.com", cfg.account_id);
    let canonical_uri = if key.is_empty() {
        format!("/{}", cfg.bucket)
    } else {
        let encoded: Vec<String> = key.split('/').map(uri_encode_segment).collect();
        format!("/{}/{}", cfg.bucket, encoded.join("/"))
    };
    let amz_date = now.format("%Y%m%dT%H%M%SZ").to_string();
    let date = now.format("%Y%m%d").to_string();
    let payload_hash = "UNSIGNED-PAYLOAD";

    // Canonical headers must be sorted; range is optional.
    let mut headers: Vec<(String, String)> = vec![
        ("host".into(), host.clone()),
        ("x-amz-content-sha256".into(), payload_hash.into()),
        ("x-amz-date".into(), amz_date.clone()),
    ];
    if let Some(r) = range {
        headers.push(("range".into(), r.to_string()));
    }
    headers.sort();
    let canonical_headers: String = headers
        .iter()
        .map(|(k, v)| format!("{k}:{v}\n"))
        .collect();
    let signed_headers: String = headers
        .iter()
        .map(|(k, _)| k.as_str())
        .collect::<Vec<_>>()
        .join(";");

    let canonical_request = format!(
        "{method}\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    );
    let scope = format!("{date}/auto/s3/aws4_request");
    let string_to_sign = format!(
        "AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{}",
        sha256_hex(canonical_request.as_bytes())
    );
    let key_bytes = signing_key(&cfg.secret_access_key, &date, "auto", "s3");
    let signature = hex::encode(hmac_sha256(&key_bytes, string_to_sign.as_bytes()));
    let authorization = format!(
        "AWS4-HMAC-SHA256 Credential={}/{scope}, SignedHeaders={signed_headers}, Signature={signature}",
        cfg.access_key_id
    );

    let mut out: Vec<(String, String)> = headers
        .into_iter()
        .filter(|(k, _)| k != "host")
        .collect();
    out.push(("authorization".into(), authorization));
    (format!("https://{host}{canonical_uri}"), out)
}

/// Signed GET (or HEAD) against R2. `range` passes through for 206 playback.
pub async fn request(
    cfg: &R2Cfg,
    method: &str,
    key: &str,
    range: Option<&str>,
) -> Result<reqwest::Response, reqwest::Error> {
    let (url, headers) = sign_get(cfg, method, key, range, chrono::Utc::now());
    let mut req = match method {
        "HEAD" => http().head(&url),
        _ => http().get(&url),
    };
    for (k, v) in headers {
        req = req.header(k, v);
    }
    req.send().await
}

/// Download `key` fully to `dest` (tmp + rename). Used by pin and cache fill.
pub async fn download_to(cfg: &R2Cfg, key: &str, dest: &Path) -> Result<(), String> {
    let resp = request(cfg, "GET", key, None).await.map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("R2 GET {key}: {}", resp.status()));
    }
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let tmp = dest.with_extension("part");
    let mut file = std::fs::File::create(&tmp).map_err(|e| e.to_string())?;
    let mut stream = resp.bytes_stream();
    use futures_util::StreamExt;
    use std::io::Write;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| e.to_string())?;
        file.write_all(&chunk).map_err(|e| e.to_string())?;
    }
    file.flush().map_err(|e| e.to_string())?;
    drop(file);
    std::fs::rename(&tmp, dest).map_err(|e| e.to_string())
}

/// Kick a background full download of `key` into the cache (deduped), then
/// enforce the LRU cap. Cheap to call on every proxied request.
pub fn spawn_cache_fill(
    cfg: R2Cfg,
    cache_dir: PathBuf,
    key: String,
    inflight: Arc<Mutex<HashSet<String>>>,
) {
    {
        let mut set = inflight.lock().unwrap();
        if !set.insert(key.clone()) {
            return;
        }
    }
    tokio::spawn(async move {
        let dest = cache_dir.join(&key);
        if !dest.exists() {
            if let Err(e) = download_to(&cfg, &key, &dest).await {
                eprintln!("cache fill {key}: {e}");
            } else {
                evict_lru(&cache_dir, CACHE_CAP_BYTES);
            }
        }
        inflight.lock().unwrap().remove(&key);
    });
}

/// Delete oldest-accessed cache files until total size fits the cap.
pub fn evict_lru(cache_dir: &Path, cap: u64) {
    let mut files: Vec<(PathBuf, u64, std::time::SystemTime)> = Vec::new();
    let mut walk = vec![cache_dir.to_path_buf()];
    while let Some(dir) = walk.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                walk.push(p);
            } else if let Ok(md) = e.metadata() {
                let atime = md.modified().unwrap_or(std::time::SystemTime::UNIX_EPOCH);
                files.push((p, md.len(), atime));
            }
        }
    }
    let mut total: u64 = files.iter().map(|(_, s, _)| s).sum();
    if total <= cap {
        return;
    }
    files.sort_by_key(|(_, _, t)| *t);
    for (path, size, _) in files {
        if total <= cap {
            break;
        }
        if std::fs::remove_file(&path).is_ok() {
            total = total.saturating_sub(size);
        }
    }
}

/// Bump mtime so LRU eviction sees this file as recently used. (No shelling
/// out — iOS forbids spawning processes.)
pub fn touch(path: &Path) {
    let _ = filetime::set_file_mtime(path, filetime::FileTime::now());
}

#[cfg(test)]
mod tests {
    use super::*;

    // AWS SigV4 documented test vector: secret wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY,
    // date 20150830, us-east-1/iam derives this signing key.
    #[test]
    fn sigv4_signing_key_matches_aws_vector() {
        let k = signing_key(
            "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            "20150830",
            "us-east-1",
            "iam",
        );
        assert_eq!(
            hex::encode(k),
            "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"
        );
    }

    #[test]
    fn sign_get_shape() {
        let cfg = R2Cfg {
            account_id: "acct".into(),
            access_key_id: "AKID".into(),
            secret_access_key: "SECRET".into(),
            bucket: "watch-transcriber-audio".into(),
        };
        let now = chrono::DateTime::parse_from_rfc3339("2026-07-28T00:00:00Z")
            .unwrap()
            .with_timezone(&chrono::Utc);
        let (url, headers) = sign_get(&cfg, "GET", "2026-07-20/213456-测试.m4a", Some("bytes=0-99"), now);
        assert!(url.starts_with("https://acct.r2.cloudflarestorage.com/watch-transcriber-audio/2026-07-20/"));
        assert!(url.contains("%E6%B5%8B%E8%AF%95")); // path segment percent-encoded
        let auth = &headers.iter().find(|(k, _)| k == "authorization").unwrap().1;
        assert!(auth.contains("Credential=AKID/20260728/auto/s3/aws4_request"));
        assert!(auth.contains("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date"));
    }
}
