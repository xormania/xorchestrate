use fixture_rust_crate::interval::Interval;

fn main() {
    assert!(!Interval::new(1, 3).contains(3));
}
