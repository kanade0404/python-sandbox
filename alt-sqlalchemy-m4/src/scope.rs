//! Scope binding -- the crown jewel.
//!
//! sqlc computes join-induced nullability *after* the fact, by re-walking the
//! FROM tree once per output column (`isTableRequired`,
//! internal/compiler/output_columns.go:428-468). That re-walk has three holes:
//!
//!   1. its INNER arm is `helper(tableRequired, tableRequired)` -- it *discards*
//!      the incoming flag, so `a LEFT JOIN (b JOIN c)` forgets that the whole
//!      right subtree is null-extended;
//!   2. it has no `RangeSubselect` arm at all, so a derived table returns
//!      `tableNotFound` and the column keeps whatever nullability it had;
//!   3. LATERAL is a `RangeSubselect` too, so it falls into the same hole.
//!
//! Doing the same computation at *bind* time fixes all three structurally: the
//! flag is carried down the tree once, every relation is stamped with it as it
//! is bound, and column resolution is then a single OR. A derived table is not
//! a special case -- it is bound like anything else, with its own analysis
//! supplying the column list.

use pg_query::protobuf::{JoinType, Node};
use pg_query::NodeEnum;

use crate::catalog::{fold, string_list};
use crate::output::Analyzer;

#[derive(Debug, Clone)]
pub struct RelColumn {
    pub name: String,
    /// Nullability *within* the relation, before any outer join is applied.
    pub nullable: bool,
}

#[derive(Debug, Clone)]
pub struct Relation {
    /// The name columns are qualified by: the alias if there is one, else the
    /// table or CTE name.
    pub alias: String,
    pub columns: Vec<RelColumn>,
    /// Set by `bind_from`: this relation sits on the null-extended side of some
    /// enclosing outer join.
    pub outer_nullable: bool,
    /// False for relations whose column list we could not determine (function
    /// scans). Star expansion and failed lookups degrade to nullable instead of
    /// erroring.
    pub shape_known: bool,
}

#[derive(Debug, Default)]
pub struct Frame {
    pub rels: Vec<Relation>,
}

#[derive(Debug, Clone)]
pub struct Cte {
    pub name: String,
    pub columns: Vec<RelColumn>,
}

/// Frames innermost-last. A correlated (LATERAL or scalar) subquery resolves
/// against its own frame first and then outward, which is exactly SQL's rule.
#[derive(Debug, Default)]
pub struct ScopeStack {
    pub frames: Vec<Frame>,
    pub ctes: Vec<Cte>,
}

impl ScopeStack {
    pub fn push_frame(&mut self) {
        self.frames.push(Frame::default());
    }

    pub fn pop_frame(&mut self) {
        self.frames.pop();
    }

    pub fn add(&mut self, rel: Relation) {
        if let Some(f) = self.frames.last_mut() {
            f.rels.push(rel);
        }
    }

    pub fn cte(&self, name: &str) -> Option<&Cte> {
        // Innermost WITH wins.
        self.ctes.iter().rev().find(|c| c.name == name)
    }

    pub fn current(&self) -> &[Relation] {
        self.frames.last().map(|f| f.rels.as_slice()).unwrap_or(&[])
    }
}

impl Analyzer<'_> {
    /// Bind one FROM-clause item, stamping `outer_nullable` onto every relation
    /// it contributes. This is the design doc's `bind_from`.
    pub fn bind_from(
        &mut self,
        node: &Node,
        outer_nullable: bool,
        scope: &mut ScopeStack,
    ) -> Result<(), String> {
        let Some(inner) = node.node.as_ref() else {
            return Ok(());
        };
        match inner {
            NodeEnum::RangeVar(v) => {
                let name = fold(&v.relname);
                let alias = v
                    .alias
                    .as_ref()
                    .map(|a| fold(&a.aliasname))
                    .unwrap_or_else(|| name.clone());
                let colnames = v
                    .alias
                    .as_ref()
                    .map(|a| string_list(&a.colnames))
                    .unwrap_or_default();

                let mut columns = if let Some(cte) = scope.cte(&name) {
                    cte.columns.clone()
                } else if let Some(t) = self.catalog.get(&name) {
                    t.columns
                        .iter()
                        .map(|c| RelColumn {
                            name: c.name.clone(),
                            nullable: !c.not_null,
                        })
                        .collect()
                } else {
                    return Err(format!(
                        "relation \"{}\" is not in the schema (and is not a CTE)",
                        v.relname
                    ));
                };
                rename(&mut columns, &colnames);
                scope.add(Relation {
                    alias,
                    columns,
                    outer_nullable,
                    shape_known: true,
                });
                Ok(())
            }

            NodeEnum::JoinExpr(j) => {
                let jt = JoinType::try_from(j.jointype).unwrap_or(JoinType::Undefined);
                let (left_flag, right_flag) = match jt {
                    // The preserved side keeps whatever flag it arrived with;
                    // the null-extended side becomes nullable.
                    JoinType::JoinLeft => (outer_nullable, true),
                    JoinType::JoinRight => (true, outer_nullable),
                    JoinType::JoinFull => (true, true),
                    // *** The sqlc fix ***: propagate, do not reset.
                    JoinType::JoinInner => (outer_nullable, outer_nullable),
                    // SEMI/ANTI cannot appear in a raw parse tree; anything else
                    // is treated as the conservative FULL.
                    _ => {
                        self.warn(format!(
                            "join type {jt:?} is not modelled; treating both sides as nullable"
                        ));
                        (true, true)
                    }
                };

                let mark = scope.current().len();
                if let Some(l) = j.larg.as_ref() {
                    self.bind_from(l, left_flag, scope)?;
                }
                let mid = scope.current().len();
                if let Some(r) = j.rarg.as_ref() {
                    self.bind_from(r, right_flag, scope)?;
                }

                if j.is_natural || !j.using_clause.is_empty() {
                    self.warn(
                        "NATURAL / USING join: merged join columns are not de-duplicated, so a \
                         bare `*` expands to both copies"
                            .to_string(),
                    );
                }

                // `FROM a JOIN b ON ... AS j` re-publishes the whole join under
                // one name. Collapse the relations this join contributed.
                if let Some(alias) = j.alias.as_ref() {
                    let Some(frame) = scope.frames.last_mut() else {
                        return Ok(());
                    };
                    let merged: Vec<RelColumn> = frame.rels[mark..]
                        .iter()
                        .flat_map(|r| {
                            r.columns.iter().map(move |c| RelColumn {
                                name: c.name.clone(),
                                nullable: c.nullable || r.outer_nullable,
                            })
                        })
                        .collect();
                    let shape_known = frame.rels[mark..].iter().all(|r| r.shape_known);
                    frame.rels.truncate(mark);
                    let mut merged = merged;
                    rename(&mut merged, &string_list(&alias.colnames));
                    frame.rels.push(Relation {
                        alias: fold(&alias.aliasname),
                        columns: merged,
                        outer_nullable,
                        shape_known,
                    });
                }
                let _ = mid;
                Ok(())
            }

            NodeEnum::RangeSubselect(s) => {
                // A derived table (and LATERAL, which is the same node with a
                // flag) is analysed recursively; the incoming outer_nullable is
                // then applied on top of the answer.
                let Some(sub) = s.subquery.as_ref() else {
                    return Err("derived table has no subquery".to_string());
                };
                let alias = s.alias.as_ref().ok_or_else(|| {
                    "subquery in FROM must have an alias".to_string()
                })?;

                scope.push_frame();
                let inner = self.analyze_query_node(sub, scope);
                scope.pop_frame();
                let inner = inner?;

                let mut columns: Vec<RelColumn> = inner
                    .iter()
                    .map(|c| RelColumn {
                        name: c.name.clone(),
                        nullable: c.nullable,
                    })
                    .collect();
                rename(&mut columns, &string_list(&alias.colnames));
                scope.add(Relation {
                    alias: fold(&alias.aliasname),
                    columns,
                    outer_nullable,
                    shape_known: true,
                });
                Ok(())
            }

            NodeEnum::RangeFunction(f) => {
                // `FROM generate_series(...) g`, `FROM unnest(x) AS t(v)`, ...
                // The shape of a set-returning function is not something this
                // engine models, so it becomes an all-nullable relation of
                // unknown shape.
                let alias_name = f
                    .alias
                    .as_ref()
                    .map(|a| fold(&a.aliasname))
                    .unwrap_or_else(|| function_scan_name(f).unwrap_or_else(|| "f".to_string()));
                let colnames = f
                    .alias
                    .as_ref()
                    .map(|a| string_list(&a.colnames))
                    .unwrap_or_default();
                let mut columns: Vec<RelColumn> = colnames
                    .iter()
                    .map(|n| RelColumn {
                        name: fold(n),
                        nullable: true,
                    })
                    .collect();
                if columns.is_empty() {
                    columns.push(RelColumn {
                        name: alias_name.clone(),
                        nullable: true,
                    });
                }
                self.warn(format!(
                    "function scan \"{alias_name}\" is not modelled; every column it \
                     contributes is reported nullable"
                ));
                scope.add(Relation {
                    alias: alias_name,
                    columns,
                    outer_nullable,
                    shape_known: false,
                });
                Ok(())
            }

            NodeEnum::RangeTableSample(s) => {
                // TABLESAMPLE only removes rows.
                if let Some(rel) = s.relation.as_ref() {
                    self.bind_from(rel, outer_nullable, scope)
                } else {
                    Ok(())
                }
            }

            NodeEnum::List(l) => {
                for item in &l.items {
                    self.bind_from(item, outer_nullable, scope)?;
                }
                Ok(())
            }

            other => Err(format!(
                "unsupported FROM item {}",
                crate::catalog::variant_name(other)
            )),
        }
    }

    /// Resolve a (possibly qualified) column reference to its nullability.
    pub fn resolve_column(
        &mut self,
        scope: &ScopeStack,
        qualifier: Option<&str>,
        column: &str,
    ) -> Result<bool, String> {
        let column = fold(column);
        let qualifier = qualifier.map(fold);
        let mut saw_unknown_shape = false;

        for frame in scope.frames.iter().rev() {
            let mut hit: Option<bool> = None;
            let mut hits = 0usize;
            for rel in &frame.rels {
                if let Some(q) = qualifier.as_ref() {
                    if &rel.alias != q {
                        continue;
                    }
                }
                if !rel.shape_known {
                    saw_unknown_shape = true;
                }
                if let Some(c) = rel.columns.iter().find(|c| c.name == column) {
                    hits += 1;
                    hit = Some(c.nullable || rel.outer_nullable);
                } else if qualifier.is_some() && !rel.shape_known {
                    // A qualified reference into a relation we cannot describe.
                    hits += 1;
                    hit = Some(true);
                }
            }
            if hits > 1 {
                return Err(format!("column reference \"{column}\" is ambiguous"));
            }
            if let Some(n) = hit {
                return Ok(n);
            }
            if let Some(q) = qualifier.as_ref() {
                // The qualifier names a relation in this frame but the column is
                // not one of its columns: that is an error, not a reason to look
                // further out.
                if frame.rels.iter().any(|r| &r.alias == q) {
                    return Err(format!(
                        "column \"{column}\" does not exist on relation \"{q}\""
                    ));
                }
            }
        }

        if saw_unknown_shape {
            self.warn(format!(
                "column \"{column}\" could not be resolved but a function scan is in scope; \
                 reporting nullable"
            ));
            return Ok(true);
        }
        match qualifier {
            Some(q) => Err(format!("unknown relation \"{q}\" in column reference")),
            None => Err(format!("unknown column \"{column}\"")),
        }
    }
}

/// Apply an alias column list (`FROM t AS x(a, b)`), which renames positionally
/// and may be shorter than the relation.
fn rename(columns: &mut [RelColumn], names: &[String]) {
    for (col, name) in columns.iter_mut().zip(names.iter()) {
        col.name = fold(name);
    }
}

fn function_scan_name(f: &pg_query::protobuf::RangeFunction) -> Option<String> {
    for item in &f.functions {
        if let Some(NodeEnum::List(l)) = item.node.as_ref() {
            for sub in &l.items {
                if let Some(NodeEnum::FuncCall(c)) = sub.node.as_ref() {
                    return string_list(&c.funcname).last().map(|s| fold(s));
                }
            }
        }
        if let Some(NodeEnum::FuncCall(c)) = item.node.as_ref() {
            return string_list(&c.funcname).last().map(|s| fold(s));
        }
    }
    None
}
