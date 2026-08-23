"""Left over from `uv init`; the entry points that matter are:

    uv run python -m altsa_gen --url <db-url> --config joins.toml --out <dir>
    ./verify.sh
"""

import sys


def main() -> int:
    print(__doc__, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
