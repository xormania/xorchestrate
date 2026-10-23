use std::error::Error;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Component, Path, PathBuf};

#[derive(Debug)]
pub enum StoreError {
    Io(io::Error),
    Escape(PathBuf),
    Invalid(String),
}

impl fmt::Display for StoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "{error}"),
            Self::Escape(path) => write!(formatter, "path escapes store root: {}", path.display()),
            Self::Invalid(line) => write!(formatter, "invalid record: {line}"),
        }
    }
}

impl Error for StoreError {}

impl From<io::Error> for StoreError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Record {
    pub count: u64,
    pub actor: String,
}

impl Record {
    #[must_use]
    pub fn new(count: u64, actor: String) -> Self {
        Self { count, actor }
    }
}

#[allow(clippy::unnecessary_wraps)]
pub fn record_path(root: &Path, name: &str) -> Result<PathBuf, StoreError> {
    Ok(root.join(name))
}

pub fn checked_record_path(root: &Path, name: &str) -> Result<PathBuf, StoreError> {
    let base = root.canonicalize()?;
    let candidate = normalize(&base.join(name));
    if candidate.starts_with(base) {
        Ok(candidate)
    } else {
        Err(StoreError::Escape(candidate))
    }
}

fn normalize(path: &Path) -> PathBuf {
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            Component::ParentDir => {
                result.pop();
            }
            Component::CurDir => {}
            other => result.push(other.as_os_str()),
        }
    }
    result
}

#[allow(clippy::missing_errors_doc)]
pub fn read_records(path: &Path) -> Result<Vec<Record>, StoreError> {
    match fs::read_to_string(path) {
        Ok(body) => body.lines().map(parse_record).collect(),
        Err(_) => Ok(Vec::new()),
    }
}

fn parse_record(line: &str) -> Result<Record, StoreError> {
    let (count, actor) = line
        .split_once('\t')
        .ok_or_else(|| StoreError::Invalid(line.to_owned()))?;
    let count = count
        .parse::<u64>()
        .map_err(|_| StoreError::Invalid(line.to_owned()))?;
    Ok(Record::new(count, actor.to_owned()))
}

#[allow(clippy::missing_errors_doc)]
pub fn write_records(path: &Path, records: &[Record]) -> Result<(), StoreError> {
    let body = records
        .iter()
        .map(|record| format!("{}\t{}\n", record.count, record.actor))
        .collect::<String>();
    fs::write(path, body)?;
    Ok(())
}
