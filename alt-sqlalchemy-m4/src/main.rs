//! `altsa-analyze` -- static per-column result nullability for PostgreSQL.
//!
//!     altsa-analyze --schema schema.sql --query query.sql
//!
//! Reads DDL and one SQL statement, prints JSON, touches no database. This is
//! the alt-SQLAlchemy "Phase 2" engine: where M3 asked a live server to DESCRIBE
//! and EXPLAIN the statement, this answers the same question from the parse tree
//! alone.

mod catalog;
mod expr;
mod funcs;
mod output;
mod params;
mod scope;

use std::process::ExitCode;
use std::time::Instant;

use serde::Serialize;

use catalog::Catalog;
use funcs::FuncTable;
use output::Analyzer;

#[derive(Serialize)]
struct ParamOut {
    index: usize,
    name: String,
    nullable: bool,
}

#[derive(Serialize)]
struct ColumnOut {
    name: String,
    nullable: bool,
}

#[derive(Serialize)]
struct Report {
    columns: Vec<ColumnOut>,
    params: Vec<ParamOut>,
    warnings: Vec<String>,
}

#[derive(Serialize)]
struct ErrorReport {
    error: String,
    warnings: Vec<String>,
}

const USAGE: &str = "\
altsa-analyze -- static result-column nullability for PostgreSQL queries

USAGE:
    altsa-analyze [--schema <file>]... (--query <file> | --query-sql <sql>)

OPTIONS:
    --schema <file>      DDL to load into the catalog. May be repeated.
    --query <file>       The SQL statement to analyse.
    --query-sql <sql>    The SQL statement, inline.
    --timing             Print analysis wall time to stderr.
    --catalog-only       Load the schema, report the catalog, and exit.
    -h, --help           This text.

Output is a single JSON object on stdout:
    {\"columns\": [{\"name\": ..., \"nullable\": ...}], \"params\": [...], \"warnings\": [...]}

Exit code is 0 on success and 1 when the query could not be analysed (in which
case stdout carries {\"error\": ..., \"warnings\": [...]}).
";

fn main() -> ExitCode {
    match run() {
        Ok(code) => code,
        Err(e) => {
            eprintln!("altsa-analyze: {e}");
            ExitCode::from(2)
        }
    }
}

struct Args {
    schemas: Vec<String>,
    query_file: Option<String>,
    query_sql: Option<String>,
    timing: bool,
    catalog_only: bool,
}

fn parse_args() -> Result<Option<Args>, String> {
    let mut args = Args {
        schemas: Vec::new(),
        query_file: None,
        query_sql: None,
        timing: false,
        catalog_only: false,
    };
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "-h" | "--help" => {
                print!("{USAGE}");
                return Ok(None);
            }
            "--schema" => args
                .schemas
                .push(it.next().ok_or("--schema needs a path")?),
            "--query" => args.query_file = Some(it.next().ok_or("--query needs a path")?),
            "--query-sql" => args.query_sql = Some(it.next().ok_or("--query-sql needs SQL")?),
            "--timing" => args.timing = true,
            "--catalog-only" => args.catalog_only = true,
            other => return Err(format!("unknown argument {other:?}\n\n{USAGE}")),
        }
    }
    Ok(Some(args))
}

fn run() -> Result<ExitCode, String> {
    let Some(args) = parse_args()? else {
        return Ok(ExitCode::SUCCESS);
    };

    let funcs = FuncTable::load()?;
    let mut warnings: Vec<String> = Vec::new();
    let mut catalog = Catalog::default();

    let started = Instant::now();
    for path in &args.schemas {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
        catalog.load(&text, &mut warnings)?;
    }
    let catalog_time = started.elapsed();

    if args.catalog_only {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "tables": catalog.len(),
                "functions": funcs.len(),
                "warnings": warnings,
            }))
            .map_err(|e| e.to_string())?
        );
        if args.timing {
            eprintln!("catalog: {:.3} ms", catalog_time.as_secs_f64() * 1000.0);
        }
        return Ok(ExitCode::SUCCESS);
    }

    let raw_query = match (&args.query_file, &args.query_sql) {
        (Some(p), None) => std::fs::read_to_string(p).map_err(|e| format!("{p}: {e}"))?,
        (None, Some(s)) => s.clone(),
        (Some(_), Some(_)) => return Err("pass --query or --query-sql, not both".to_string()),
        (None, None) => return Err(format!("no query given\n\n{USAGE}")),
    };

    let query_started = Instant::now();
    let result = analyze(&raw_query, &catalog, &funcs, &mut warnings);
    let query_time = query_started.elapsed();

    let code = match result {
        Ok((columns, params)) => {
            let report = Report {
                columns,
                params,
                warnings,
            };
            println!(
                "{}",
                serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?
            );
            ExitCode::SUCCESS
        }
        Err(e) => {
            let report = ErrorReport { error: e, warnings };
            println!(
                "{}",
                serde_json::to_string_pretty(&report).map_err(|e| e.to_string())?
            );
            ExitCode::from(1)
        }
    };

    if args.timing {
        eprintln!(
            "catalog: {:.3} ms  analysis: {:.3} ms",
            catalog_time.as_secs_f64() * 1000.0,
            query_time.as_secs_f64() * 1000.0
        );
    }
    Ok(code)
}

fn analyze(
    raw_query: &str,
    catalog: &Catalog,
    funcs: &FuncTable,
    warnings: &mut Vec<String>,
) -> Result<(Vec<ColumnOut>, Vec<ParamOut>), String> {
    // `${name}` -> `$n`, exactly as M3's annotation front-end does, so a corpus
    // `query.sql` can be fed in verbatim.
    let rewritten = params::rewrite(raw_query.trim().trim_end_matches(';'))?;
    let parsed =
        pg_query::parse(&rewritten.sql).map_err(|e| format!("query parse error: {e}"))?;

    let stmts: Vec<_> = parsed
        .protobuf
        .stmts
        .iter()
        .filter_map(|s| s.stmt.as_ref())
        .collect();
    if stmts.len() != 1 {
        return Err(format!(
            "expected exactly one statement, found {}",
            stmts.len()
        ));
    }

    let mut analyzer = Analyzer::new(catalog, funcs);
    let columns = analyzer.analyze_statement(stmts[0]);
    warnings.extend(analyzer.warnings.iter().cloned());
    let columns = columns?;

    Ok((
        columns
            .into_iter()
            .map(|c| ColumnOut {
                name: c.name,
                nullable: c.nullable,
            })
            .collect(),
        rewritten
            .params
            .into_iter()
            .map(|p| ParamOut {
                index: p.index,
                name: p.name,
                nullable: p.nullable,
            })
            .collect(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SCHEMA: &str = "
        CREATE TABLE a (id integer PRIMARY KEY, label text NOT NULL, note text);
        CREATE TABLE b (id integer PRIMARY KEY, a_id integer NOT NULL, amount numeric NOT NULL);
        CREATE TABLE c (id integer PRIMARY KEY, b_id integer NOT NULL, tag text NOT NULL);
    ";

    fn nullability(sql: &str) -> Vec<(String, bool)> {
        let funcs = FuncTable::load().unwrap();
        let mut warnings = Vec::new();
        let mut catalog = Catalog::default();
        catalog.load(SCHEMA, &mut warnings).unwrap();
        let (cols, _) = analyze(sql, &catalog, &funcs, &mut warnings).unwrap();
        cols.into_iter().map(|c| (c.name, c.nullable)).collect()
    }

    #[test]
    fn inner_join_preserves_not_null() {
        assert_eq!(
            nullability("SELECT a.id, b.amount FROM a JOIN b ON b.a_id = a.id"),
            vec![("id".into(), false), ("amount".into(), false)]
        );
    }

    #[test]
    fn left_join_nested_right_is_the_sqlc_bug() {
        assert_eq!(
            nullability(
                "SELECT a.id, b.id AS b_id, c.id AS c_id \
                 FROM a LEFT JOIN (b JOIN c ON c.b_id = b.id) ON b.a_id = a.id"
            ),
            vec![
                ("id".into(), false),
                ("b_id".into(), true),
                ("c_id".into(), true)
            ]
        );
    }

    #[test]
    fn ungrouped_aggregate_is_nullable_but_count_is_not() {
        assert_eq!(
            nullability("SELECT sum(b.amount) AS total, count(*) AS n FROM b"),
            vec![("total".into(), true), ("n".into(), false)]
        );
    }

    #[test]
    fn grouped_aggregate_over_not_null_argument_is_not_null() {
        assert_eq!(
            nullability("SELECT sum(b.amount) AS total FROM b GROUP BY b.a_id"),
            vec![("total".into(), false)]
        );
    }

    #[test]
    fn coalesce_with_non_null_tail_is_not_null() {
        assert_eq!(
            nullability("SELECT coalesce(a.note, '') AS note FROM a"),
            vec![("note".into(), false)]
        );
    }

    #[test]
    fn union_ors_the_branches() {
        assert_eq!(
            nullability("SELECT label AS v FROM a UNION ALL SELECT note FROM a"),
            vec![("v".into(), true)]
        );
    }

    #[test]
    fn star_expands_with_join_nullability() {
        assert_eq!(
            nullability("SELECT b.* FROM a LEFT JOIN b ON b.a_id = a.id"),
            vec![
                ("id".into(), true),
                ("a_id".into(), true),
                ("amount".into(), true)
            ]
        );
    }

    #[test]
    fn recursive_cte_degrades_to_nullable_instead_of_failing() {
        assert_eq!(
            nullability(
                "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5) \
                 SELECT n FROM t"
            ),
            vec![("n".into(), true)]
        );
    }

    #[test]
    fn filter_and_window_force_nullable_except_count() {
        assert_eq!(
            nullability(
                "SELECT count(*) FILTER (WHERE b.amount > 0) AS c, \
                        sum(b.amount) FILTER (WHERE b.amount > 0) AS s, \
                        sum(b.amount) OVER () AS w, \
                        row_number() OVER () AS rn \
                 FROM b GROUP BY b.a_id"
            ),
            vec![
                ("c".into(), false),
                ("s".into(), true),
                ("w".into(), true),
                ("rn".into(), false)
            ]
        );
    }

    #[test]
    fn returning_clauses_are_analysed() {
        assert_eq!(
            nullability("UPDATE a SET label = 'x' WHERE id = 1 RETURNING id, note"),
            vec![("id".into(), false), ("note".into(), true)]
        );
    }

    #[test]
    fn alias_markers_win() {
        assert_eq!(
            nullability("SELECT a.note AS \"note!\", a.label AS \"label?\" FROM a"),
            vec![("note".into(), false), ("label".into(), true)]
        );
    }
}
