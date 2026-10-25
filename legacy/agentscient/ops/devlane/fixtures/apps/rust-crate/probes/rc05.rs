use std::fs;

use fixture_rust_crate::store::read_records;

fn main() {
    let path = std::env::temp_dir().join(format!("rc05-{}", std::process::id()));
    fs::create_dir_all(&path).expect("create unreadable store path");
    let is_ok = read_records(&path).is_ok();
    fs::remove_dir_all(path).expect("remove unreadable store path");
    assert!(!is_ok);
}
