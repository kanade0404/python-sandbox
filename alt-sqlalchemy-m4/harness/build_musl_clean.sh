#!/bin/sh
# Timed CLEAN musl build, for gate R1. Wipes target-musl/ first so the number is
# a from-scratch build rather than an incremental one.
set -eu
M4_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

echo "== wiping target-musl/ =="
docker run --rm --platform linux/amd64 -v "$M4_DIR":/home/rust/src \
    alpine:3.20 sh -c 'rm -rf /home/rust/src/target-musl'

echo "== timed clean musl build =="
time sh "$M4_DIR/build-musl.sh"
