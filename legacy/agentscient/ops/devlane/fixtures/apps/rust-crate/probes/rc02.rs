use std::fs;

use fixture_rust_crate::store::record_path;

fn main() {
    let root = std::env::temp_dir().join(format!("rc02-{}", std::process::id()));
    fs::create_dir_all(&root).expect("create root");
    let is_ok = record_path(&root, "../outside").is_ok();
    fs::remove_dir_all(root).expect("remove root");
    assert!(!is_ok);
}
