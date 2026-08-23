"""Not an entry point -- a signpost. See README.md.

  generate      uv run python -m altsa_sqlgen --url <dsn> --queries queries --out generated
  corpus        uv run python -m corpus.runner --url <dsn>
  oracle        uv run python -m corpus.oracle --url <dsn>
  every gate    ./verify.sh
"""

from __future__ import annotations

if __name__ == "__main__":
    print(__doc__)
