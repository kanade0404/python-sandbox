"""The annotation front-end: a .sql file -> a list of :class:`QueryBlock`.

The grammar is a deliberately SMALL subset of SQG's (see
`/Users/kanade0404/work/sqg/sqg/src/parser/sql.grammar`). SQG parses SQL; we do
NOT. The only thing this module understands about SQL is where its *string
literals, comments and quoted identifiers* are, because a `${var}` inside one
of those is not a parameter.

    -- QUERY get_order_with_user :one
    -- OVERRIDE created_at :notnull          (optional, 0..n per block)
    SELECT o.id, u.email AS "email?"
    FROM orders o JOIN users u ON u.id = o.user_id
    WHERE o.id = ${order_id};

Three things are annotations; everything else is opaque SQL:

  * `-- QUERY <name> :one|:many|:exec` starts a block. The block runs to the
    next `-- QUERY` line or EOF.
  * `${var}` and `${var?}` mark parameters. `?` means the parameter is
    nullable, i.e. typed `T | None`. First occurrence fixes the order;
    repeating a name reuses the same parameter.
  * `-- OVERRIDE <column> :notnull|:nullable` forces a result column's
    nullability by NAME. (The sqlx-style `AS "col!"` / `AS "col?"` alias
    markers are the other, preferred spelling; those are handled in
    `describe.py` because they only become visible after the server has told
    us the result column names.)

Everything here is pure -- no database, no I/O -- so the H1 unit tests can
exercise it directly.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .errors import GenerationError

QueryKind = Literal["one", "many", "exec"]
_KINDS: frozenset[str] = frozenset({"one", "many", "exec"})

NullabilityOverride = Literal["notnull", "nullable"]

_QUERY_LINE = re.compile(r"^\s*--\s*QUERY\s+(?P<rest>.*?)\s*$")
_OVERRIDE_LINE = re.compile(
    r"^\s*--\s*OVERRIDE\s+(?P<col>[^\s:]+)\s*:(?P<mode>notnull|nullable)\s*$"
)
_QUERY_HEAD = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+:(?P<kind>[a-z]+)$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ParamRef:
    """One `${var}` occurrence: a half-open span over the block's SQL text."""

    name: str
    start: int
    end: int
    nullable: bool


@dataclass(frozen=True, slots=True)
class Param:
    """A distinct parameter, in first-occurrence order."""

    name: str
    index: int  # 1-based, matches the `$n` used for DESCRIBE
    nullable: bool


@dataclass(frozen=True, slots=True)
class QueryBlock:
    """One `-- QUERY` block, parsed but not yet described."""

    name: str
    kind: QueryKind
    sql: str
    """The raw SQL body, `${var}` markers still in place."""
    refs: tuple[ParamRef, ...]
    params: tuple[Param, ...]
    overrides: tuple[tuple[str, NullabilityOverride], ...]
    source: str
    line: int
    """1-based line of the `-- QUERY` header, for error messages."""

    def render_psycopg(self) -> str:
        """SQL as embedded in generated code: `${var}` -> `%(var)s`.

        Any other `%` is doubled, because psycopg's client-side binder treats
        `%` as its own escape character whenever a parameter mapping is passed.
        Blocks with no parameters are emitted verbatim and executed without a
        mapping, so no doubling is needed (or wanted) there.
        """
        if not self.params:
            return self.sql
        return self._render(lambda ref: f"%({ref.name})s", escape_percent=True)

    def render_native(self) -> str:
        """SQL as sent to PREPARE: `${var}` -> `$n`. Never contains `%%`."""
        order = {p.name: p.index for p in self.params}
        return self._render(lambda ref: f"${order[ref.name]}", escape_percent=False)

    def _render(self, sub: Callable[[ParamRef], str], *, escape_percent: bool) -> str:
        out: list[str] = []
        pos = 0
        for ref in self.refs:
            chunk = self.sql[pos : ref.start]
            out.append(chunk.replace("%", "%%") if escape_percent else chunk)
            out.append(sub(ref))
            pos = ref.end
        tail = self.sql[pos:]
        out.append(tail.replace("%", "%%") if escape_percent else tail)
        return "".join(out)


# --------------------------------------------------------------------------
# 1. the SQL scanner -- find `${var}` OUTSIDE literals/comments/identifiers
# --------------------------------------------------------------------------


def scan_param_refs(sql: str) -> tuple[ParamRef, ...]:
    """Locate every `${var}` that is really a parameter.

    Skipped regions (a `${...}` inside one of these is left alone):
      * `'...'` string literals, with `''` as the escape
      * `$tag$...$tag$` dollar-quoted strings
      * `E'...'` escape strings, where `\\'` also escapes
      * `"..."` quoted identifiers -- this is also where the sqlx-style
        `AS "col!"` markers live, so they must never be touched
      * `-- ...` line comments and `/* ... */` block comments (PostgreSQL
        block comments nest)
    """
    refs: list[ParamRef] = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]

        # -- line comment
        if c == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
            continue

        # /* nesting block comment */
        if c == "/" and sql.startswith("/*", i):
            depth = 1
            i += 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth += 1
                    i += 2
                elif sql.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue

        # E'...' escape string (backslash escapes)
        if c in "eE" and i + 1 < n and sql[i + 1] == "'" and not _is_ident_char(sql, i - 1):
            i += 2
            while i < n:
                if sql[i] == "\\":
                    i += 2
                elif sql[i] == "'":
                    if sql.startswith("''", i):
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            continue

        # '...' string literal
        if c == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if sql.startswith("''", i):
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            continue

        # "..." quoted identifier ("" escapes)
        if c == '"':
            i += 1
            while i < n:
                if sql[i] == '"':
                    if sql.startswith('""', i):
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            continue

        # ${var} / ${var?} -- the thing we are actually looking for.
        # Checked BEFORE dollar-quoting: `$tag$` cannot start with `{`.
        if c == "$" and i + 1 < n and sql[i + 1] == "{":
            j = sql.find("}", i)
            if j < 0:
                raise GenerationError(f"unterminated ${{...}} at offset {i}")
            body = sql[i + 2 : j]
            nullable = body.endswith("?")
            name = body[:-1] if nullable else body
            if not _IDENT.match(name):
                raise GenerationError(
                    f"invalid parameter name ${{{body}}} -- expected an identifier, "
                    f"optionally followed by '?'"
                )
            refs.append(ParamRef(name=name, start=i, end=j + 1, nullable=nullable))
            i = j + 1
            continue

        # $tag$ dollar-quoted string
        if c == "$":
            m = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$").match(sql, i)
            if m is not None:
                close = m.group(0)
                j = sql.find(close, m.end())
                i = n if j < 0 else j + len(close)
                continue

        i += 1

    return tuple(refs)


def _is_ident_char(sql: str, i: int) -> bool:
    return 0 <= i < len(sql) and (sql[i].isalnum() or sql[i] == "_")


# --------------------------------------------------------------------------
# 2. block splitting
# --------------------------------------------------------------------------


def parse_file(text: str, *, source: str) -> tuple[QueryBlock, ...]:
    """Split an annotated .sql file into blocks. Order is preserved."""
    lines = text.splitlines()
    starts: list[int] = [i for i, ln in enumerate(lines) if _QUERY_LINE.match(ln)]
    if not starts:
        raise GenerationError("no `-- QUERY <name> :one|:many|:exec` block found", source=source)

    preamble = "\n".join(lines[: starts[0]]).strip()
    if preamble and not all(ln.lstrip().startswith("--") or not ln.strip() for ln in lines[: starts[0]]):
        raise GenerationError(
            "text before the first `-- QUERY` block must be comments only", source=source
        )

    blocks: list[QueryBlock] = []
    seen: dict[str, int] = {}
    bounds = starts + [len(lines)]
    for k, head_idx in enumerate(starts):
        blocks.append(
            _parse_block(lines[head_idx : bounds[k + 1]], head_line=head_idx + 1, source=source)
        )
        b = blocks[-1]
        if b.name in seen:
            raise GenerationError(
                f"duplicate query name (first seen on line {seen[b.name]})",
                source=source,
                query=b.name,
            )
        seen[b.name] = b.line
    return tuple(blocks)


def _parse_block(lines: list[str], *, head_line: int, source: str) -> QueryBlock:
    head = _QUERY_LINE.match(lines[0])
    assert head is not None  # only called on lines that matched
    rest = head.group("rest")
    m = _QUERY_HEAD.match(rest)
    if m is None:
        raise GenerationError(
            f"malformed header `-- QUERY {rest}` -- expected "
            f"`-- QUERY <name> :one|:many|:exec`",
            source=f"{source}:{head_line}",
        )
    name: str = m.group("name")
    kind_text: str = m.group("kind")
    if kind_text not in ("one", "many", "exec"):
        raise GenerationError(
            f"unknown result kind `:{kind_text}` -- expected one of "
            f"{', '.join(':' + k for k in sorted(_KINDS))}",
            source=f"{source}:{head_line}",
            query=name,
        )
    kind: QueryKind = kind_text

    # OVERRIDE directives may only appear directly under the header, before SQL.
    overrides: list[tuple[str, NullabilityOverride]] = []
    body_start = 1
    for idx in range(1, len(lines)):
        ln = lines[idx]
        if not ln.strip():
            body_start = idx + 1
            continue
        om = _OVERRIDE_LINE.match(ln)
        if om is not None:
            col = om.group("col")
            mode = om.group("mode")
            assert mode in ("notnull", "nullable")
            overrides.append((col, mode))
            body_start = idx + 1
            continue
        if ln.lstrip().startswith("--"):
            body_start = idx + 1
            continue
        body_start = idx
        break

    sql = "\n".join(lines[body_start:]).strip()
    if not sql:
        raise GenerationError("empty SQL body", source=f"{source}:{head_line}", query=name)
    # A trailing `;` is fine in the file but must not reach PREPARE.
    sql = sql.rstrip().rstrip(";").rstrip()

    try:
        refs = scan_param_refs(sql)
    except GenerationError as exc:
        raise GenerationError(exc.raw_message, source=f"{source}:{head_line}", query=name) from None

    params: list[Param] = []
    by_name: dict[str, Param] = {}
    for ref in refs:
        existing = by_name.get(ref.name)
        if existing is None:
            p = Param(name=ref.name, index=len(params) + 1, nullable=ref.nullable)
            params.append(p)
            by_name[ref.name] = p
        elif existing.nullable != ref.nullable:
            raise GenerationError(
                f"parameter ${{{ref.name}}} is used both as required and as "
                f"nullable (`${{{ref.name}?}}`) -- pick one",
                source=f"{source}:{head_line}",
                query=name,
            )

    dupes = {c for c, _ in overrides if sum(1 for c2, _ in overrides if c2 == c) > 1}
    if dupes:
        raise GenerationError(
            f"duplicate OVERRIDE for column(s) {', '.join(sorted(dupes))}",
            source=f"{source}:{head_line}",
            query=name,
        )

    return QueryBlock(
        name=name,
        kind=kind,
        sql=sql,
        refs=refs,
        params=tuple(params),
        overrides=tuple(overrides),
        source=source,
        line=head_line,
    )


# --------------------------------------------------------------------------
# 3. sqlx-style alias markers on RESULT column names
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColumnName:
    """A result column name split into its field name and its marker.

    Mirrors `ColumnDecl::parse` in
    `sqlx-macros-core/src/query/output.rs`: the identifier runs up to the
    first of `:`, `!`, `?`.
    """

    field: str
    override: NullabilityOverride | None


def parse_column_name(raw: str) -> ColumnName:
    """`"email?"` -> (`email`, nullable); `"id!"` -> (`id`, notnull)."""
    cut = min((raw.find(ch) for ch in ":!?" if ch in raw), default=-1)
    if cut < 0:
        return ColumnName(field=raw, override=None)
    ident, marker = raw[:cut], raw[cut:]
    if marker == "!":
        return ColumnName(field=ident, override="notnull")
    if marker == "?":
        return ColumnName(field=ident, override="nullable")
    if marker.startswith(":"):
        raise GenerationError(
            f"column alias {raw!r} uses a `:type` override -- type overrides are "
            f"not supported in M3 (nullability overrides `!` and `?` are)"
        )
    raise GenerationError(
        f"column alias {raw!r} has a trailing marker {marker!r} -- expected "
        f"exactly `!` (force NOT NULL) or `?` (force nullable)"
    )
