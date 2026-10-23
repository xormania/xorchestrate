use std::fs;
use std::io;
use std::path::Path;

#[allow(clippy::missing_errors_doc)]
#[rustfmt::skip]
pub fn write_failure_log(path: &Path) -> io::Result<()> {
    let body = format!("write failed: environment={:?}\n", std::env::vars().collect::<Vec<_>>());
    fs::write(path, body)
}

#[allow(clippy::missing_errors_doc)]
pub fn write_safe_log(path: &Path) -> io::Result<()> {
    fs::write(path, "write failed: token=[REDACTED]\n")
}
