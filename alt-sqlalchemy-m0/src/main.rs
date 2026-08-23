//! M0 build-feasibility spike for pg_query.rs.
//!
//! This is a micro-prototype of the M4 bind-time nullability walk: it parses a
//! query with nested LEFT JOINs (including a derived-table subquery), walks the
//! protobuf AST to describe each JoinExpr, and exercises fingerprint/normalize/
//! deparse — the primitives an M4 SQL-analysis CLI would depend on.

use pg_query::protobuf::{JoinType, Node};
use pg_query::NodeEnum;

const TARGET_SQL: &str = "SELECT u.email, o.id FROM users u \
LEFT JOIN orders o ON o.user_id = u.id \
LEFT JOIN (SELECT * FROM payments) p ON p.order_id = o.id";

fn main() {
    println!("== pg_query.rs M0 build-feasibility spike ==");
    println!("pg_query crate: 6.2.0 (wraps libpg_query 17-6.2.2, PostgreSQL 17 parser)\n");

    section_a_parse();
    section_b_join_walk();
    section_c_fingerprint_normalize();
    section_d_deparse_roundtrip();

    println!("\n== all sections completed OK ==");
}

fn section_a_parse() {
    println!("--- (a) parse ---");
    println!("SQL: {TARGET_SQL}");
    let result = pg_query::parse(TARGET_SQL).expect("parse should succeed");
    println!(
        "parsed OK: {} statement(s), tables referenced: {:?}",
        result.protobuf.stmts.len(),
        result.tables
    );
    println!();
}

fn section_b_join_walk() {
    println!("--- (b) AST walk: JoinExpr jointype + relation names/aliases ---");
    let result = pg_query::parse(TARGET_SQL).expect("parse should succeed");

    // Use the crate's built-in flattened walker to find every JoinExpr in the
    // tree (it already knows how to descend into JoinExpr.larg/rarg/quals and
    // RangeSubselect.subquery), then describe each side directly from the
    // JoinExpr's own larg/rarg fields.
    let mut join_count = 0;
    for (node_ref, _depth, _context, _has_filter_columns) in result.protobuf.nodes() {
        if let pg_query::NodeRef::JoinExpr(j) = node_ref {
            join_count += 1;
            let jointype = JoinType::try_from(j.jointype).unwrap_or(JoinType::Undefined);
            println!("JoinExpr #{join_count}: jointype = {:?}", jointype);
            println!("  left  side: {}", describe_side(&j.larg));
            println!("  right side: {}", describe_side(&j.rarg));
        }
    }
    println!("total JoinExpr nodes found: {join_count}");
    println!();
}

/// Describe one side (larg/rarg) of a JoinExpr: table name + alias for a plain
/// RangeVar, "derived table" + alias for a RangeSubselect, or a marker for a
/// nested JoinExpr (which will also be reported separately since the outer
/// walker flattens the whole tree).
fn describe_side(node: &Option<Box<Node>>) -> String {
    let Some(node_enum) = node.as_ref().and_then(|n| n.node.as_ref()) else {
        return "<none>".to_string();
    };
    match node_enum {
        NodeEnum::RangeVar(v) => {
            let alias = v
                .alias
                .as_ref()
                .map(|a| a.aliasname.as_str())
                .unwrap_or("<no alias>");
            format!("base table `{}` (alias: {})", v.relname, alias)
        }
        NodeEnum::RangeSubselect(s) => {
            let alias = s
                .alias
                .as_ref()
                .map(|a| a.aliasname.as_str())
                .unwrap_or("<no alias>");
            format!("derived table (RangeSubselect, i.e. subquery) (alias: {alias})")
        }
        NodeEnum::JoinExpr(_) => "nested JoinExpr (reported separately above/below)".to_string(),
        other => format!("<other node: {other:?}>"),
    }
}

fn section_c_fingerprint_normalize() {
    println!("--- (c) fingerprint + normalize ---");

    let q1 = "SELECT * FROM users WHERE id = 1";
    let q2 = "SELECT * FROM users WHERE id = 42";
    let fp1 = pg_query::fingerprint(q1).expect("fingerprint q1");
    let fp2 = pg_query::fingerprint(q2).expect("fingerprint q2");
    println!("q1: {q1}");
    println!("q2: {q2}");
    println!("fingerprint(q1).hex = {}", fp1.hex);
    println!("fingerprint(q2).hex = {}", fp2.hex);
    println!(
        "fingerprints equal despite differing literals: {}",
        fp1.hex == fp2.hex
    );
    assert_eq!(
        fp1.hex, fp2.hex,
        "fingerprint should be literal-insensitive"
    );

    let normalized = pg_query::normalize(q1).expect("normalize q1");
    println!("normalize(q1) = {normalized}");
    println!();
}

fn section_d_deparse_roundtrip() {
    println!("--- (d) deparse round-trip: parse -> deparse -> parse, fingerprints equal ---");
    let original = TARGET_SQL;
    let parsed1 = pg_query::parse(original).expect("first parse");
    let deparsed = parsed1
        .deparse()
        .expect("deparse should succeed on a full statement");
    println!("deparsed SQL: {deparsed}");

    let parsed2 = pg_query::parse(&deparsed).expect("second parse (of deparsed SQL) should succeed");
    // Sanity: re-deparsing the second parse gives the same text (stable round-trip).
    let deparsed2 = parsed2.deparse().expect("second deparse");
    println!("round-trip stable (deparse(parse(deparsed)) == deparsed): {}", deparsed == deparsed2);
    assert_eq!(deparsed, deparsed2, "deparse should be idempotent after one round-trip");

    let fp_original = pg_query::fingerprint(original).expect("fingerprint original");
    let fp_deparsed = pg_query::fingerprint(&deparsed).expect("fingerprint deparsed");
    println!("fingerprint(original SQL).hex = {}", fp_original.hex);
    println!("fingerprint(deparsed SQL).hex = {}", fp_deparsed.hex);
    println!(
        "fingerprints equal across deparse round-trip: {}",
        fp_original.hex == fp_deparsed.hex
    );
    assert_eq!(
        fp_original.hex, fp_deparsed.hex,
        "fingerprint should be stable across a parse -> deparse -> parse round-trip"
    );
    println!();
}
