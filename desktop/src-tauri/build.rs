fn commit_hash() -> String {
    let output = std::process::Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .unwrap();
    String::from_utf8(output.stdout).unwrap()
}

fn main() {
    let hash = commit_hash();
    println!("cargo:rerun-if-env-changed=COMMIT_HASH");
    println!("cargo:rustc-env=COMMIT_HASH={}", hash);

    // Analytics. The names must match what `analytics.rs` reads with `option_env!`, or changing
    // the key does not trigger a rebuild and the old one stays baked in. This said APTABASE_KEY.
    println!("cargo:rerun-if-env-changed=APTABASE_APP_KEY");
    println!("cargo:rerun-if-env-changed=APTABASE_BASE_URL");

    tauri_build::build();
}
