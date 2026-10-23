#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Interval {
    start: i64,
    end: i64,
}

impl Interval {
    #[must_use]
    pub const fn new(start: i64, end: i64) -> Self {
        Self { start, end }
    }

    #[must_use]
    pub const fn contains(&self, v: i64) -> bool {
        self.start <= v && v <= self.end
    }

    pub fn values(&self) -> std::ops::Range<i64> {
        self.start..self.end
    }
}
