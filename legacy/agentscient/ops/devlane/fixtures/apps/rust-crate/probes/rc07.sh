#!/bin/sh
grep -Eq '^anyhow *= *"\*"' Cargo.toml
test $? -ne 0
