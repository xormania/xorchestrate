use fixture_rust_crate::interval::Interval;

#[test]
fn values_are_half_open() {
    assert_eq!(Interval::new(1, 3).values().collect::<Vec<_>>(), vec![1, 2]);
}
