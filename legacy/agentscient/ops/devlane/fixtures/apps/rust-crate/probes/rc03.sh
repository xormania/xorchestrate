#!/bin/sh
command -v cargo >/dev/null || exit 127
cargo test --offline --locked --quiet --test rc03
CARGO_TARGET_DIR=target/fixture-probes cargo run --offline --locked --quiet --example rc03
