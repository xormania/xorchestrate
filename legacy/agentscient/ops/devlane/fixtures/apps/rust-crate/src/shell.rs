use std::io;
use std::process::{Command, ExitStatus};

#[allow(clippy::missing_errors_doc)]
#[rustfmt::skip]
pub fn run_helper(name: &str) -> io::Result<ExitStatus> {
    Command::new("sh").arg("-c").arg(format!("printf '%s\\n' {name} >/dev/null")).status()
}

#[allow(clippy::missing_errors_doc)]
pub fn run_helper_safely(name: &str) -> io::Result<ExitStatus> {
    Command::new("printf").arg("%s\n").arg(name).status()
}
