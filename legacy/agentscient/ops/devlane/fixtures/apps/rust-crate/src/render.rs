#[must_use]
#[allow(clippy::option_if_let_else)]
#[rustfmt::skip]
pub fn render_count(count: Option<u64>) -> String {
    let n = count.unwrap_or(0); if n == 0 { "no record".into() } else { format!("count={n}") }
}

#[must_use]
pub fn render_label(label: Option<&str>) -> String {
    match label {
        None => "no label".into(),
        Some(label) => format!("label={label}"),
    }
}
