//! Which URLs a webview may navigate to.
//!
//! Every window loads its own bundled `index.html` and nothing in the frontend
//! navigates away from that origin — in-app links go through `tauri-plugin-opener`
//! and open in the system browser. So a navigation to anywhere else is either a bug
//! or a link that arrived inside untrusted content: a transcript, or a summary the
//! model wrote from one.

/// Exact-match the app's own origins.
///
/// Matching is on the *whole* host, never a prefix or a suffix. Tauri's own
/// `is_local_url` compared only the first subdomain, which let `http://tauri.evil.com`
/// pass as a trusted local origin on Windows and reach IPC (GHSA-7gmj-67g7-phm9 /
/// CVE-2026-42184). The dependency is pinned past that fix in `Cargo.toml`; this guard
/// exists so a regression there cannot reach us a second time.
pub fn is_app_url(url: &tauri::Url) -> bool {
    match url.scheme() {
        // Served by Tauri itself on macOS and Linux.
        "tauri" | "asset" => true,
        // Windows cannot serve custom schemes, so the same content is mapped onto
        // `http://<scheme>.localhost`.
        "http" | "https" => match url.host_str() {
            Some("tauri.localhost") | Some("asset.localhost") => true,
            // The Vite dev server only. A release build serves from `tauri://`, so
            // allowing plain localhost there would widen the origin for nothing.
            Some("localhost") | Some("127.0.0.1") => cfg!(debug_assertions),
            _ => false,
        },
        _ => false,
    }
}

/// Cancel any navigation that would take a window off the app origin.
///
/// Returning `false` cancels it. Opening the URL externally is deliberately *not* done
/// here — `components/markdown.tsx` routes link clicks through the opener plugin, and
/// keeping the two independent means neither masks a regression in the other. This is
/// the backstop for whatever slips past the frontend.
pub fn allow_navigation(url: &tauri::Url) -> bool {
    if is_app_url(url) {
        return true;
    }
    tracing::warn!("blocked webview navigation to {}", url);
    false
}

#[cfg(test)]
mod tests {
    use super::is_app_url;

    fn url(value: &str) -> tauri::Url {
        value.parse().expect("valid url")
    }

    #[test]
    fn app_origins_are_allowed() {
        assert!(is_app_url(&url("tauri://localhost/index.html")));
        assert!(is_app_url(&url("http://tauri.localhost/index.html")));
        assert!(is_app_url(&url("http://asset.localhost/recording.wav")));
        assert!(is_app_url(&url("asset://localhost/recording.wav")));
    }

    #[test]
    fn the_cve_shape_is_rejected() {
        // A first-subdomain-only check accepted every one of these.
        assert!(!is_app_url(&url("http://tauri.evil.com/")));
        assert!(!is_app_url(&url("http://asset.attacker.test/")));
        // ...and a naive suffix check would accept these instead.
        assert!(!is_app_url(&url("https://tauri.localhost.evil.com/")));
        assert!(!is_app_url(&url("https://evil.com/tauri.localhost")));
    }

    #[test]
    fn ordinary_remote_and_exotic_schemes_are_rejected() {
        assert!(!is_app_url(&url("https://example.com/")));
        assert!(!is_app_url(&url("javascript:alert(1)")));
        assert!(!is_app_url(&url("file:///etc/passwd")));
        assert!(!is_app_url(&url("vibe://download/?url=https://evil.com/x.bin")));
    }
}
