use fixture_rust_crate::render::{render_count, render_label};

#[test]
fn present_values_are_rendered() {
    assert_eq!(render_count(Some(2)), "count=2");
    assert_eq!(render_label(Some("ready")), "label=ready");
}
