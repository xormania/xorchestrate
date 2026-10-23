use std::fs;

use fixture_rust_crate::shell::run_helper;

fn main() {
    let marker = std::env::temp_dir().join(format!("rc01-{}", std::process::id()));
    drop(fs::remove_file(&marker));
    run_helper(&format!("literal; touch {}", marker.display())).expect("run helper");
    let exists = marker.exists();
    drop(fs::remove_file(marker));
    assert!(!exists);
}
