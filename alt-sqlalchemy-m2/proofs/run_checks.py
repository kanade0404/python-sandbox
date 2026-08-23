"""Runtime half of C8: execute the leakage / no-Any checks and report."""

from __future__ import annotations

import sys

from proofs.proof_positive import (
    assert_no_any_in_public_signatures,
    assert_no_sqlalchemy_leakage,
)


def main() -> int:
    leaks = assert_no_sqlalchemy_leakage()
    anys = assert_no_any_in_public_signatures()
    ok = not leaks and not anys
    print(f"C8 runtime half: {'PASS' if ok else 'FAIL'}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
