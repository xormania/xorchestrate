use std::env;
use std::fs;
use std::path::Path;

use fixture_rust_crate::render::render_count;
use fixture_rust_crate::store::{checked_record_path, read_records, write_records, Record};

fn refusal(class: &str, expected: &str, found: &str, needed: &str) {
    eprintln!("{class}: expected {expected}; found {found}; needed {needed}");
}

fn valid_identity(identity: &str) -> bool {
    let Some((name, address)) = identity.rsplit_once('<') else {
        return false;
    };
    !name.trim().is_empty() && address.ends_with('>') && address.len() > 2
}

fn run() -> anyhow::Result<i32> {
    let args = env::args().skip(1).collect::<Vec<_>>();
    match args.as_slice() {
        [verb, root, name] if verb == "read" => {
            let path = checked_record_path(Path::new(root), name)?;
            let records = read_records(&path)?;
            println!(
                "{}",
                render_count(records.last().map(|record| record.count))
            );
            Ok(0)
        }
        [verb, root, name, count] if verb == "write" => {
            let identity = env::var("WF_AGENT").unwrap_or_default();
            if !valid_identity(&identity) {
                refusal(
                    "identity",
                    "Name <address>",
                    if identity.is_empty() {
                        "unset"
                    } else {
                        "malformed"
                    },
                    "export WF_AGENT",
                );
                return Ok(2);
            }
            let count = count.parse::<u64>()?;
            fs::create_dir_all(root)?;
            let path = checked_record_path(Path::new(root), name)?;
            let actor = hex::encode(identity.as_bytes());
            write_records(&path, &[Record::new(count, actor)])?;
            Ok(0)
        }
        _ => {
            refusal(
                "arguments",
                "read ROOT NAME or write ROOT NAME COUNT",
                "another argument shape",
                "name one supported verb",
            );
            Ok(2)
        }
    }
}

fn main() {
    let status = match run() {
        Ok(status) => status,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    };
    std::process::exit(status);
}
