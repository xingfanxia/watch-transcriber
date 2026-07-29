//! Secure sync-token storage.
//!
//! Tokens live in the platform secure store — iOS Protected Data (Keychain),
//! Android Keystore-encrypted SharedPreferences, macOS login keychain — as a
//! single JSON blob under one entry. Nothing token-shaped ever touches plain
//! files; `sync_config.json` on disk holds only non-secret settings.

use serde::{Deserialize, Serialize};

const SERVICE: &str = "ai.ax.echowall";
const USER: &str = "sync-tokens";

#[derive(Clone, Serialize, Deserialize)]
pub struct SyncTokens {
    pub github_pat: String,
    pub r2_account_id: String,
    pub r2_access_key_id: String,
    pub r2_secret_access_key: String,
}

/// Install the platform credential store once. Errors are returned as strings
/// so callers can surface "secure store unavailable" instead of panicking.
fn ensure_store() -> Result<(), String> {
    if keyring_core::get_default_store().is_some() {
        return Ok(());
    }
    #[cfg(target_os = "ios")]
    {
        use apple_native_keyring_store::protected;
        keyring_core::set_default_store(protected::Store::new().map_err(|e| e.to_string())?);
    }
    #[cfg(target_os = "macos")]
    {
        use apple_native_keyring_store::keychain;
        keyring_core::set_default_store(keychain::Store::new().map_err(|e| e.to_string())?);
    }
    #[cfg(target_os = "android")]
    {
        use android_native_keyring_store::Store;
        // Store::new panics (not errs) if Kotlin didn't hand over the ndk
        // context (MainActivity -> Keyring.initializeNdkContext). Degrade to
        // an error the setup page can show instead of a crash loop.
        let store = std::panic::catch_unwind(Store::new)
            .map_err(|_| "Android Keystore 不可用 (ndk context 未初始化)".to_string())?
            .map_err(|e| e.to_string())?;
        keyring_core::set_default_store(store);
    }
    #[cfg(not(any(target_os = "ios", target_os = "macos", target_os = "android")))]
    {
        return Err("no secure store on this platform".into());
    }
    #[allow(unreachable_code)]
    Ok(())
}

fn entry() -> Result<keyring_core::Entry, String> {
    ensure_store()?;
    // iOS: allow background access after first device unlock so launch-sync
    // works before the app is foregrounded (default policy is per-unlock).
    #[cfg(target_os = "ios")]
    {
        let mods = std::collections::HashMap::from([("access-policy", "after-first-unlock")]);
        return keyring_core::Entry::new_with_modifiers(SERVICE, USER, &mods)
            .map_err(|e| e.to_string());
    }
    #[allow(unreachable_code)]
    keyring_core::Entry::new(SERVICE, USER).map_err(|e| e.to_string())
}

/// Personal-build seeding: if ECHOWALL_SEED_* env vars were present at
/// COMPILE time (option_env!), plant them into the secure store on first run
/// so the owner's own build needs zero token entry. Public/CI builds never
/// set these, so release binaries contain nothing. Values live only in the
/// keychain afterwards — same lifecycle as manually entered tokens.
pub fn seed_from_build_env() {
    let (Some(pat), Some(acct), Some(akid), Some(secret)) = (
        option_env!("ECHOWALL_SEED_GITHUB_PAT"),
        option_env!("ECHOWALL_SEED_R2_ACCOUNT_ID"),
        option_env!("ECHOWALL_SEED_R2_ACCESS_KEY_ID"),
        option_env!("ECHOWALL_SEED_R2_SECRET"),
    ) else {
        return;
    };
    if load().is_some() {
        return;
    }
    let _ = save(&SyncTokens {
        github_pat: pat.into(),
        r2_account_id: acct.into(),
        r2_access_key_id: akid.into(),
        r2_secret_access_key: secret.into(),
    });
}

pub fn save(tokens: &SyncTokens) -> Result<(), String> {
    let blob = serde_json::to_string(tokens).map_err(|e| e.to_string())?;
    entry()?.set_password(&blob).map_err(|e| e.to_string())
}

pub fn load() -> Option<SyncTokens> {
    let blob = entry().ok()?.get_password().ok()?;
    serde_json::from_str(&blob).ok()
}
