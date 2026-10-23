use std::fs;

use fixture_rust_crate::audit::write_failure_log;

fn main() {
    let path = std::env::temp_dir().join(format!("rc04-{}", std::process::id()));
    std::env::set_var("FIXTURE_TOKEN", "rc04-secret");
    write_failure_log(&path).expect("write failure log");
    let body = fs::read_to_string(&path).expect("read failure log");
    std::env::remove_var("FIXTURE_TOKEN");
    fs::remove_file(path).expect("remove failure log");
    assert!(!body.contains("rc04-secret"));
}
