"""H1 -- the annotation front-end, unit-tested without a database.

Everything in `altsa_sqlgen.annotations` is pure, which is the point: the part
of the pipeline that decides what counts as a parameter must be checkable
without a server, because a mistake there silently changes the SQL that gets
sent.
"""

from __future__ import annotations

import pytest

from altsa_sqlgen.annotations import (
    parse_column_name,
    parse_file,
    scan_param_refs,
)
from altsa_sqlgen.errors import GenerationError


def names(sql: str) -> list[str]:
    return [r.name for r in scan_param_refs(sql)]


# --------------------------------------------------------------------------
# 1. QUERY block splitting
# --------------------------------------------------------------------------


def test_single_block() -> None:
    (block,) = parse_file(
        "-- QUERY get_user :one\nSELECT id FROM users WHERE id = ${uid}", source="q.sql"
    )
    assert block.name == "get_user"
    assert block.kind == "one"
    assert block.sql == "SELECT id FROM users WHERE id = ${uid}"
    assert [p.name for p in block.params] == ["uid"]


def test_multiple_blocks_keep_file_order() -> None:
    blocks = parse_file(
        "-- QUERY a :one\nSELECT 1 AS x\n\n"
        "-- QUERY b :many\nSELECT 2 AS x\n\n"
        "-- QUERY c :exec\nDELETE FROM t",
        source="q.sql",
    )
    assert [b.name for b in blocks] == ["a", "b", "c"]
    assert [b.kind for b in blocks] == ["one", "many", "exec"]


def test_leading_comments_are_allowed() -> None:
    blocks = parse_file(
        "-- a file header\n-- spanning two lines\n\n-- QUERY a :one\nSELECT 1 AS x",
        source="q.sql",
    )
    assert len(blocks) == 1


def test_comments_inside_a_block_stay_with_the_sql() -> None:
    (block,) = parse_file(
        "-- QUERY a :one\n-- why this query exists\nSELECT 1 AS x\n-- trailing note",
        source="q.sql",
    )
    assert "trailing note" in block.sql


def test_trailing_semicolon_is_stripped() -> None:
    (block,) = parse_file("-- QUERY a :one\nSELECT 1 AS x;\n", source="q.sql")
    assert block.sql == "SELECT 1 AS x"


@pytest.mark.parametrize("kind", ["one", "many", "exec"])
def test_every_kind_parses(kind: str) -> None:
    (block,) = parse_file(f"-- QUERY a :{kind}\nSELECT 1 AS x", source="q.sql")
    assert block.kind == kind


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(GenerationError, match="unknown result kind"):
        parse_file("-- QUERY a :single\nSELECT 1 AS x", source="q.sql")


def test_missing_kind_is_rejected() -> None:
    with pytest.raises(GenerationError, match="malformed header"):
        parse_file("-- QUERY a\nSELECT 1 AS x", source="q.sql")


def test_no_block_is_rejected() -> None:
    with pytest.raises(GenerationError, match="no `-- QUERY"):
        parse_file("SELECT 1", source="q.sql")


def test_empty_body_is_rejected() -> None:
    with pytest.raises(GenerationError, match="empty SQL body"):
        parse_file("-- QUERY a :one\n\n", source="q.sql")


def test_duplicate_names_are_rejected() -> None:
    with pytest.raises(GenerationError, match="duplicate query name"):
        parse_file(
            "-- QUERY a :one\nSELECT 1 AS x\n-- QUERY a :one\nSELECT 2 AS x", source="q.sql"
        )


# --------------------------------------------------------------------------
# 2. ${var} extraction
# --------------------------------------------------------------------------


def test_plain_params_in_order() -> None:
    assert names("SELECT ${a}, ${b} FROM t WHERE x = ${c}") == ["a", "b", "c"]


def test_repeated_param_is_one_parameter() -> None:
    (block,) = parse_file(
        "-- QUERY a :many\nSELECT * FROM t WHERE ${x}::int IS NULL OR id = ${x}",
        source="q.sql",
    )
    assert [p.name for p in block.params] == ["x"]
    assert len(block.refs) == 2
    assert block.render_native().count("$1") == 2
    assert block.render_psycopg().count("%(x)s") == 2


def test_nullable_marker() -> None:
    (block,) = parse_file("-- QUERY a :exec\nINSERT INTO t VALUES (${v?})", source="q.sql")
    assert block.params[0].nullable is True


def test_mixed_nullability_for_one_name_is_rejected() -> None:
    with pytest.raises(GenerationError, match="both as required and as"):
        parse_file("-- QUERY a :many\nSELECT ${v}, ${v?} FROM t", source="q.sql")


def test_invalid_param_name_is_rejected() -> None:
    with pytest.raises(GenerationError, match="invalid parameter name"):
        scan_param_refs("SELECT ${1bad} FROM t")


def test_unterminated_is_rejected() -> None:
    with pytest.raises(GenerationError, match="unterminated"):
        scan_param_refs("SELECT ${oops FROM t")


# -- the interesting half: ${...} inside things that are NOT parameters ----


def test_inside_single_quoted_string_is_not_a_param() -> None:
    assert names("SELECT '${nope}' AS lit, ${yes} FROM t") == ["yes"]


def test_doubled_quote_escape_does_not_end_the_string() -> None:
    # The '' is an escaped quote, so ${nope} is still inside the literal.
    assert names("SELECT 'it''s ${nope}' AS lit, ${yes} FROM t") == ["yes"]


def test_inside_escape_string_is_not_a_param() -> None:
    assert names(r"SELECT E'a\' ${nope}' AS lit, ${yes} FROM t") == ["yes"]


def test_inside_dollar_quoted_string_is_not_a_param() -> None:
    assert names("SELECT $$ ${nope} $$ AS lit, ${yes} FROM t") == ["yes"]


def test_inside_tagged_dollar_quoted_string_is_not_a_param() -> None:
    assert names("SELECT $tag$ ${nope} $tag$ AS lit, ${yes} FROM t") == ["yes"]


def test_inside_quoted_identifier_is_not_a_param() -> None:
    assert names('SELECT c AS "${nope}", ${yes} FROM t') == ["yes"]


def test_inside_line_comment_is_not_a_param() -> None:
    assert names("SELECT 1 -- ${nope}\n, ${yes} FROM t") == ["yes"]


def test_inside_block_comment_is_not_a_param() -> None:
    assert names("SELECT /* ${nope} */ ${yes} FROM t") == ["yes"]


def test_nested_block_comments() -> None:
    # PostgreSQL block comments nest; a naive scan for the first `*/` would
    # end the comment early and treat ${nope2} as a parameter.
    assert names("SELECT /* a /* b ${nope1} */ ${nope2} */ ${yes} FROM t") == ["yes"]


def test_dollar_quote_containing_a_brace_is_still_a_string() -> None:
    assert names("SELECT $q$ {} ${nope} $q$ AS lit, ${yes} FROM t") == ["yes"]


# --------------------------------------------------------------------------
# 3. rendering
# --------------------------------------------------------------------------


def test_render_native_numbers_params() -> None:
    (block,) = parse_file(
        "-- QUERY a :many\nSELECT * FROM t WHERE a = ${x} AND b = ${y}", source="q.sql"
    )
    assert block.render_native() == "SELECT * FROM t WHERE a = $1 AND b = $2"


def test_render_psycopg_uses_named_placeholders() -> None:
    (block,) = parse_file(
        "-- QUERY a :many\nSELECT * FROM t WHERE a = ${x}", source="q.sql"
    )
    assert block.render_psycopg() == "SELECT * FROM t WHERE a = %(x)s"


def test_render_psycopg_doubles_literal_percent() -> None:
    # psycopg's client-side binder treats % as its own escape whenever a
    # parameter mapping is passed, so a LIKE pattern has to be doubled.
    (block,) = parse_file(
        "-- QUERY a :many\nSELECT * FROM t WHERE name LIKE '%' || ${q} || '%'",
        source="q.sql",
    )
    assert block.render_psycopg() == (
        "SELECT * FROM t WHERE name LIKE '%%' || %(q)s || '%%'"
    )
    # ... but the DESCRIBE rendering must NOT be doubled, since it goes to the
    # server verbatim.
    assert "%%" not in block.render_native()


def test_render_psycopg_leaves_percent_alone_when_there_are_no_params() -> None:
    (block,) = parse_file(
        "-- QUERY a :many\nSELECT * FROM t WHERE name LIKE '%x%'", source="q.sql"
    )
    assert block.render_psycopg() == "SELECT * FROM t WHERE name LIKE '%x%'"


# --------------------------------------------------------------------------
# 4. override markers on result column names (sqlx `col!` / `col?`)
# --------------------------------------------------------------------------


def test_plain_name_has_no_override() -> None:
    parsed = parse_column_name("email")
    assert parsed.field == "email"
    assert parsed.override is None


def test_bang_forces_not_null_and_is_stripped() -> None:
    parsed = parse_column_name("email!")
    assert parsed.field == "email"
    assert parsed.override == "notnull"


def test_question_forces_nullable_and_is_stripped() -> None:
    parsed = parse_column_name("email?")
    assert parsed.field == "email"
    assert parsed.override == "nullable"


def test_type_override_is_rejected_in_m3() -> None:
    with pytest.raises(GenerationError, match="type overrides are"):
        parse_column_name("email: String")


def test_unknown_marker_is_rejected() -> None:
    with pytest.raises(GenerationError, match="trailing marker"):
        parse_column_name("email!!")


# --------------------------------------------------------------------------
# 5. OVERRIDE directives
# --------------------------------------------------------------------------


def test_override_directive_parses() -> None:
    (block,) = parse_file(
        "-- QUERY a :many\n-- OVERRIDE total :notnull\n-- OVERRIDE note :nullable\n"
        "SELECT 1 AS total, 2 AS note",
        source="q.sql",
    )
    assert block.overrides == (("total", "notnull"), ("note", "nullable"))
    assert block.sql.startswith("SELECT")


def test_duplicate_override_is_rejected() -> None:
    with pytest.raises(GenerationError, match="duplicate OVERRIDE"):
        parse_file(
            "-- QUERY a :many\n-- OVERRIDE x :notnull\n-- OVERRIDE x :nullable\nSELECT 1 AS x",
            source="q.sql",
        )


def test_override_after_sql_is_treated_as_sql_not_a_directive() -> None:
    # Directives only bind directly under the header; anything after SQL has
    # started is an ordinary comment and must not silently take effect.
    (block,) = parse_file(
        "-- QUERY a :many\nSELECT 1 AS x\n-- OVERRIDE x :notnull", source="q.sql"
    )
    assert block.overrides == ()
