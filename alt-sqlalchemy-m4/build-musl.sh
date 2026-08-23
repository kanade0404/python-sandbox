#!/bin/sh
# Static x86_64 musl build, using the recipe M0 established, plus two additions.
#
#  * BINDGEN_EXTRA_CLANG_ARGS -- M0's. libpg_query is a C library, so pg_query.rs
#    runs bindgen at build time and bindgen must be pointed at the musl
#    sysroot's headers or it picks up the host's.
#
#  * CARGO_TARGET_DIR=target-musl -- keeps the container's Linux artefacts out of
#    the host's `target/`, which otherwise ends up holding two hosts' build
#    scripts in one directory.
#
#  * DOCS_RS=1 -- pg_query's build.rs regenerates `src/protobuf.rs` with
#    prost-build whenever it thinks `protoc` is available, and prost-build then
#    fails in this image because there is no protoc in it. `DOCS_RS` is the
#    crate's own documented escape hatch (build.rs: `is_built_by_docs_rs`) and
#    takes the `skipping protobuf generation` branch, which uses the generated
#    `src/protobuf.rs` the crate already ships -- the same file the native macOS
#    build uses. See evidence/build_musl.log for the failure it avoids.
set -eu

M4_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IMAGE=messense/rust-musl-cross:x86_64-musl
TARGET=x86_64-unknown-linux-musl

docker run --rm \
    --platform linux/amd64 \
    -v "$M4_DIR":/home/rust/src \
    -e CARGO_TARGET_DIR=/home/rust/src/target-musl \
    -e DOCS_RS=1 \
    -e BINDGEN_EXTRA_CLANG_ARGS="--sysroot=/usr/local/musl/$TARGET -I/usr/local/musl/$TARGET/include" \
    "$IMAGE" \
    cargo build --release --target "$TARGET"

ls -l "$M4_DIR/target-musl/$TARGET/release/altsa-analyze"
file "$M4_DIR/target-musl/$TARGET/release/altsa-analyze" 2>/dev/null || true
