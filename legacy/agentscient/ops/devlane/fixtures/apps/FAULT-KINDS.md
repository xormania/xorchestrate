# Fixture fault kinds

The fixture apps share these stable fault kinds so calibration rates can be
compared across toolchains. Each manifest cites the id and supplies the exact
declared and practiced quotation pair for its planted instance.

| id | kind | declared | practiced | idiom | probe shape |
|---|---|---|---|---|---|
| K1 | untrusted string reaching a shell or argv | input is passed as arguments | string interpolation builds a shell line | Python, PHP, Rust | runtime check |
| K2 | a path escaping its root | paths remain below their root | a joined path is used without normalization and a prefix check | Python, PHP, Rust | failing test |
| K3 | a loose comparison (false as zero) | absent and zero remain distinct | truthiness or loose equality conflates them | Python, PHP, Rust | failing test |
| K4 | a secret in the environment or a log | credentials never leave the process | a token appears in inherited environment or logging | Python, PHP, Rust | runtime check |
| K5 | an error swallowed | failures are reported | a broad catch or discarded result returns a default | Python, PHP, Rust | failing test |
| K6 | a boundary off by one (half-open intervals) | ranges are half-open | the end boundary is treated as inclusive | Python, PHP, Rust | failing test |
| K7 | a test proving against a stub | tests exercise the real boundary | a stub replaces the boundary and cannot fail | Python, PHP, Rust | runtime check |
| K8 | an oracle taken from the code under test | expected values are independent | the subject computes its own expected value | Python, PHP | static grep |
| K9 | an unpinned or drifted dependency | dependencies are pinned and locked | a range is used or the lock disagrees with the manifest | Python, PHP, Rust | static grep |
