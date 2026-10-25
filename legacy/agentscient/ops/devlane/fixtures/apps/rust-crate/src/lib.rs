//! The fixture crate: a records store with a two-verb bin. Apparatus, never a product.

pub mod audit;
pub mod interval;
pub mod render;
pub mod shell;
pub mod store;

#[cfg(test)]
mod tests {
    #[test]
    fn modules_are_linked() {
        assert_eq!(crate::render::render_label(Some("linked")), "label=linked");
    }
}
