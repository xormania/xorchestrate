use fixture_rust_crate::render::render_count;

fn main() {
    assert_eq!(render_count(Some(0)), "count=0");
}
