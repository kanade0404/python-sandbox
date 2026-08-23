//! Statement-level analysis: FROM binding, CTEs, set operations, and the
//! projection that turns a target list into named, nullability-resolved output
//! columns.

use pg_query::protobuf::{GroupingSetKind, Node, SelectStmt, SetOperation};
use pg_query::NodeEnum;

use crate::catalog::{fold, string_list, variant_name, Catalog};
use crate::funcs::FuncTable;
use crate::scope::ScopeStack;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutColumn {
    pub name: String,
    pub nullable: bool,
}

/// Everything an expression needs to know about the SELECT level it sits in.
#[derive(Debug, Clone, Copy)]
pub struct ExprCtx {
    /// A plain `GROUP BY a, b`: every emitted group has at least one row, so an
    /// aggregate over a non-null argument cannot be NULL. GROUPING SETS /
    /// ROLLUP / CUBE / `GROUP BY ()` do NOT count -- they emit super-aggregate
    /// rows that summarise zero rows of some columns.
    pub grouped: bool,
}

pub struct Analyzer<'a> {
    pub catalog: &'a Catalog,
    pub funcs: &'a FuncTable,
    pub warnings: Vec<String>,
    depth: usize,
}

const MAX_DEPTH: usize = 64;

impl<'a> Analyzer<'a> {
    pub fn new(catalog: &'a Catalog, funcs: &'a FuncTable) -> Self {
        Self {
            catalog,
            funcs,
            warnings: Vec::new(),
            depth: 0,
        }
    }

    pub fn warn(&mut self, msg: String) {
        if !self.warnings.contains(&msg) {
            self.warnings.push(msg);
        }
    }

    /// Entry point: analyse one parsed statement.
    pub fn analyze_statement(&mut self, node: &Node) -> Result<Vec<OutColumn>, String> {
        let mut scope = ScopeStack::default();
        match node.node.as_ref() {
            Some(NodeEnum::SelectStmt(_)) => self.analyze_query_node(node, &mut scope),
            Some(NodeEnum::InsertStmt(s)) => {
                self.analyze_returning(&s.returning_list, s.relation.as_ref(), &mut scope)
            }
            Some(NodeEnum::UpdateStmt(s)) => {
                self.analyze_returning(&s.returning_list, s.relation.as_ref(), &mut scope)
            }
            Some(NodeEnum::DeleteStmt(s)) => {
                self.analyze_returning(&s.returning_list, s.relation.as_ref(), &mut scope)
            }
            Some(other) => Err(format!(
                "statement type {} produces no analysable result columns",
                variant_name(other)
            )),
            None => Err("empty statement".to_string()),
        }
    }

    fn analyze_returning(
        &mut self,
        returning: &[Node],
        relation: Option<&pg_query::protobuf::RangeVar>,
        scope: &mut ScopeStack,
    ) -> Result<Vec<OutColumn>, String> {
        if returning.is_empty() {
            return Ok(Vec::new());
        }
        scope.push_frame();
        if let Some(rel) = relation {
            let node = Node {
                node: Some(NodeEnum::RangeVar(rel.clone())),
            };
            self.bind_from(&node, false, scope)?;
        }
        let cols = self.project(returning, scope, ExprCtx { grouped: false });
        scope.pop_frame();
        cols
    }

    /// Analyse a node that stands for a query: a `SelectStmt` (possibly a set
    /// operation or a VALUES list). Used for the top level, for derived tables,
    /// for CTE bodies and for scalar subqueries.
    pub fn analyze_query_node(
        &mut self,
        node: &Node,
        scope: &mut ScopeStack,
    ) -> Result<Vec<OutColumn>, String> {
        match node.node.as_ref() {
            Some(NodeEnum::SelectStmt(s)) => self.analyze_select(s, scope),
            Some(other) => Err(format!(
                "expected a SELECT here, found {}",
                variant_name(other)
            )),
            None => Err("empty subquery".to_string()),
        }
    }

    pub fn analyze_select(
        &mut self,
        sel: &SelectStmt,
        scope: &mut ScopeStack,
    ) -> Result<Vec<OutColumn>, String> {
        self.depth += 1;
        if self.depth > MAX_DEPTH {
            self.depth -= 1;
            return Err("query nesting is deeper than this analyser supports".to_string());
        }
        let result = self.analyze_select_inner(sel, scope);
        self.depth -= 1;
        result
    }

    fn analyze_select_inner(
        &mut self,
        sel: &SelectStmt,
        scope: &mut ScopeStack,
    ) -> Result<Vec<OutColumn>, String> {
        let ctes_pushed = self.bind_ctes(sel, scope)?;
        let result = self.analyze_select_body(sel, scope);
        for _ in 0..ctes_pushed {
            scope.ctes.pop();
        }
        result
    }

    fn bind_ctes(&mut self, sel: &SelectStmt, scope: &mut ScopeStack) -> Result<usize, String> {
        let Some(with) = sel.with_clause.as_ref() else {
            return Ok(0);
        };
        let mut pushed = 0usize;
        for cte in &with.ctes {
            let Some(NodeEnum::CommonTableExpr(cte)) = cte.node.as_ref() else {
                continue;
            };
            let name = fold(&cte.ctename);
            let Some(body) = cte.ctequery.as_ref() else {
                continue;
            };

            let mut columns = if with.recursive {
                // A recursive CTE's own name is in scope inside its own body,
                // which this engine does not model. Only the *non-recursive*
                // term (the left branch of the UNION) is analysed, for the
                // column list; every column is then reported nullable, which is
                // sound whatever the recursive term does.
                self.warn(format!(
                    "WITH RECURSIVE \"{name}\": the recursive term is not analysed; every \
                     column of this CTE is reported nullable"
                ));
                scope.push_frame();
                let inner = match anchor_select(body) {
                    Some(anchor) => self.analyze_select(anchor, scope),
                    None => self.analyze_query_node(body, scope),
                };
                scope.pop_frame();
                match inner {
                    Ok(cols) => cols
                        .into_iter()
                        .map(|c| crate::scope::RelColumn {
                            name: c.name,
                            nullable: true,
                        })
                        .collect(),
                    // Fall back to the `WITH t(a, b)` column list if there is
                    // one; a recursive CTE without one that we cannot analyse is
                    // a real error.
                    Err(e) if cte.aliascolnames.is_empty() => return Err(e),
                    Err(_) => Vec::new(),
                }
            } else {
                scope.push_frame();
                let inner = self.analyze_query_node(body, scope);
                scope.pop_frame();
                inner?
                    .into_iter()
                    .map(|c| crate::scope::RelColumn {
                        name: c.name,
                        nullable: c.nullable,
                    })
                    .collect()
            };

            // `WITH x(a, b) AS (...)` renames positionally, and is the only
            // column list a recursive CTE we could not analyse leaves us.
            let aliases = string_list(&cte.aliascolnames);
            if columns.is_empty() {
                columns = aliases
                    .iter()
                    .map(|a| crate::scope::RelColumn {
                        name: fold(a),
                        nullable: true,
                    })
                    .collect();
            }
            for (col, alias) in columns.iter_mut().zip(aliases.iter()) {
                col.name = fold(alias);
            }

            scope.ctes.push(crate::scope::Cte { name, columns });
            pushed += 1;
        }
        Ok(pushed)
    }

    fn analyze_select_body(
        &mut self,
        sel: &SelectStmt,
        scope: &mut ScopeStack,
    ) -> Result<Vec<OutColumn>, String> {
        // -- set operation -------------------------------------------------
        let op = SetOperation::try_from(sel.op).unwrap_or(SetOperation::Undefined);
        if matches!(
            op,
            SetOperation::SetopUnion | SetOperation::SetopIntersect | SetOperation::SetopExcept
        ) {
            let left = sel
                .larg
                .as_ref()
                .ok_or_else(|| "set operation with no left branch".to_string())?;
            let right = sel
                .rarg
                .as_ref()
                .ok_or_else(|| "set operation with no right branch".to_string())?;
            let l = self.analyze_select(left, scope)?;
            let r = self.analyze_select(right, scope)?;
            if l.len() != r.len() {
                return Err(format!(
                    "set operation branches have {} and {} columns",
                    l.len(),
                    r.len()
                ));
            }
            // PostgreSQL takes the output names from the FIRST branch, and a
            // column is nullable exactly when ANY branch's is -- the disjunction.
            // sqlc reads only the left branch, which is where its UNION bug lives.
            return Ok(l
                .into_iter()
                .zip(r)
                .map(|(a, b)| OutColumn {
                    name: a.name,
                    nullable: a.nullable || b.nullable,
                })
                .collect());
        }

        // -- VALUES --------------------------------------------------------
        if !sel.values_lists.is_empty() {
            let width = sel
                .values_lists
                .iter()
                .filter_map(|row| match row.node.as_ref() {
                    Some(NodeEnum::List(l)) => Some(l.items.len()),
                    _ => None,
                })
                .max()
                .unwrap_or(0);
            self.warn(
                "VALUES-derived relation: PostgreSQL reports its columns with no base-table \
                 attribution, so every column is reported nullable (see corpus \
                 sqlx/values_derived_all_nullable)"
                    .to_string(),
            );
            return Ok((1..=width)
                .map(|i| OutColumn {
                    name: format!("column{i}"),
                    nullable: true,
                })
                .collect());
        }

        // -- plain SELECT --------------------------------------------------
        scope.push_frame();
        let bound = (|| -> Result<(), String> {
            for item in &sel.from_clause {
                self.bind_from(item, false, scope)?;
            }
            Ok(())
        })();
        if let Err(e) = bound {
            scope.pop_frame();
            return Err(e);
        }

        let ctx = ExprCtx {
            grouped: self.is_plain_group_by(&sel.group_clause),
        };
        let cols = self.project(&sel.target_list, scope, ctx);
        scope.pop_frame();
        cols
    }

    /// `GROUP BY a, b` yields one row per non-empty group. GROUPING SETS,
    /// ROLLUP, CUBE and `GROUP BY ()` all add super-aggregate rows, so they are
    /// treated as "no GROUP BY" -- the conservative side.
    fn is_plain_group_by(&mut self, group_clause: &[Node]) -> bool {
        if group_clause.is_empty() {
            return false;
        }
        for item in group_clause {
            if let Some(NodeEnum::GroupingSet(gs)) = item.node.as_ref() {
                let kind = GroupingSetKind::try_from(gs.kind).unwrap_or(GroupingSetKind::Undefined);
                self.warn(format!(
                    "GROUP BY {kind:?} emits super-aggregate rows; aggregates are reported \
                     nullable as if there were no GROUP BY"
                ));
                return false;
            }
        }
        true
    }

    // ----------------------------------------------------------------- projection

    fn project(
        &mut self,
        target_list: &[Node],
        scope: &mut ScopeStack,
        ctx: ExprCtx,
    ) -> Result<Vec<OutColumn>, String> {
        let mut out: Vec<OutColumn> = Vec::new();
        for item in target_list {
            let Some(NodeEnum::ResTarget(res)) = item.node.as_ref() else {
                return Err(format!(
                    "unexpected target list entry {}",
                    item.node
                        .as_ref()
                        .map(variant_name)
                        .unwrap_or_else(|| "<empty>".to_string())
                ));
            };
            let Some(val) = res.val.as_ref() else {
                continue;
            };

            // Star expansion happens before anything else: `*` and `t.*` are not
            // expressions.
            if let Some(star) = star_target(val) {
                out.extend(self.expand_star(star, scope)?);
                continue;
            }

            let nullable = self.expr_nullable(val, scope, ctx)?;
            // Only an explicit alias can carry a `!` / `?` marker. A name
            // PostgreSQL derives is taken verbatim -- notably `?column?`, which
            // would otherwise look like a malformed marker.
            let (name, forced) = if res.name.is_empty() {
                (
                    figure_colname(val).unwrap_or_else(|| "?column?".to_string()),
                    None,
                )
            } else {
                split_marker(&res.name)?
            };
            out.push(OutColumn {
                name,
                nullable: match forced {
                    Some(Marker::NotNull) => false,
                    Some(Marker::Nullable) => true,
                    None => nullable,
                },
            });
        }
        Ok(out)
    }

    fn expand_star(
        &mut self,
        star: StarTarget<'_>,
        scope: &mut ScopeStack,
    ) -> Result<Vec<OutColumn>, String> {
        let rels = scope.current();
        let mut out = Vec::new();
        let mut matched = false;
        for rel in rels {
            if let StarTarget::Qualified(q) = star {
                if rel.alias != fold(q) {
                    continue;
                }
            }
            matched = true;
            if !rel.shape_known {
                self.warn(format!(
                    "`*` over relation \"{}\" whose shape is unknown; its columns are omitted",
                    rel.alias
                ));
                continue;
            }
            for c in &rel.columns {
                out.push(OutColumn {
                    name: c.name.clone(),
                    nullable: c.nullable || rel.outer_nullable,
                });
            }
        }
        if !matched {
            return match star {
                StarTarget::Qualified(q) => Err(format!("unknown relation \"{q}\" in \"{q}.*\"")),
                StarTarget::Bare => Err("`SELECT *` with no FROM clause".to_string()),
            };
        }
        Ok(out)
    }
}

/// The anchor (non-recursive) term of a `WITH RECURSIVE` body: the leftmost
/// branch of its set operation, which by definition cannot reference the CTE
/// itself and so can be analysed without the CTE being in scope.
fn anchor_select(body: &Node) -> Option<&SelectStmt> {
    let Some(NodeEnum::SelectStmt(sel)) = body.node.as_ref() else {
        return None;
    };
    let mut current: &SelectStmt = sel;
    while SetOperation::try_from(current.op) != Ok(SetOperation::SetopNone) {
        current = current.larg.as_deref()?;
    }
    Some(current)
}

#[derive(Debug, Clone, Copy)]
enum StarTarget<'a> {
    Bare,
    Qualified(&'a str),
}

fn star_target(node: &Node) -> Option<StarTarget<'_>> {
    let Some(NodeEnum::ColumnRef(cr)) = node.node.as_ref() else {
        return None;
    };
    let last_is_star = matches!(
        cr.fields.last().and_then(|f| f.node.as_ref()),
        Some(NodeEnum::AStar(_))
    );
    if !last_is_star {
        return None;
    }
    match cr.fields.len() {
        1 => Some(StarTarget::Bare),
        2 => match cr.fields[0].node.as_ref() {
            Some(NodeEnum::String(s)) => Some(StarTarget::Qualified(&s.sval)),
            _ => Some(StarTarget::Bare),
        },
        // `schema.table.*`
        _ => match cr.fields[cr.fields.len() - 2].node.as_ref() {
            Some(NodeEnum::String(s)) => Some(StarTarget::Qualified(&s.sval)),
            _ => Some(StarTarget::Bare),
        },
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Marker {
    NotNull,
    Nullable,
}

/// The sqlx-style alias markers M3 honours: `AS "id!"` forces NOT NULL,
/// `AS "email?"` forces nullable. Mirrors
/// `altsa_sqlgen.annotations.parse_column_name`.
fn split_marker(raw: &str) -> Result<(String, Option<Marker>), String> {
    let cut = raw.find([':', '!', '?']);
    let Some(cut) = cut else {
        return Ok((raw.to_string(), None));
    };
    let (ident, marker) = raw.split_at(cut);
    match marker {
        "!" => Ok((ident.to_string(), Some(Marker::NotNull))),
        "?" => Ok((ident.to_string(), Some(Marker::Nullable))),
        m if m.starts_with(':') => Err(format!(
            "column alias {raw:?} uses a `:type` override -- type overrides are not \
             supported (nullability overrides `!` and `?` are)"
        )),
        m => Err(format!(
            "column alias {raw:?} has a trailing marker {m:?} -- expected exactly `!` \
             (force NOT NULL) or `?` (force nullable)"
        )),
    }
}

/// PostgreSQL's `FigureColname`, in the subset that matters here.
pub fn figure_colname(node: &Node) -> Option<String> {
    let inner = node.node.as_ref()?;
    match inner {
        NodeEnum::ColumnRef(cr) => match cr.fields.last().and_then(|f| f.node.as_ref()) {
            Some(NodeEnum::String(s)) => Some(fold(&s.sval)),
            _ => None,
        },
        NodeEnum::FuncCall(f) => string_list(&f.funcname).last().map(|s| fold(s)),
        NodeEnum::TypeCast(c) => c
            .arg
            .as_ref()
            .and_then(|a| figure_colname(a))
            .or_else(|| {
                c.type_name
                    .as_ref()
                    .and_then(|t| string_list(&t.names).last().map(|s| fold(s)))
            }),
        NodeEnum::CollateExpr(e) => e.arg.as_ref().and_then(|a| figure_colname(a)),
        NodeEnum::AExpr(e) => {
            if pg_query::protobuf::AExprKind::try_from(e.kind)
                == Ok(pg_query::protobuf::AExprKind::AexprNullif)
            {
                Some("nullif".to_string())
            } else {
                None
            }
        }
        NodeEnum::CaseExpr(e) => e
            .defresult
            .as_ref()
            .and_then(|d| figure_colname(d))
            .or_else(|| Some("case".to_string())),
        NodeEnum::CoalesceExpr(_) => Some("coalesce".to_string()),
        NodeEnum::MinMaxExpr(e) => Some(
            match pg_query::protobuf::MinMaxOp::try_from(e.op) {
                Ok(pg_query::protobuf::MinMaxOp::IsLeast) => "least",
                _ => "greatest",
            }
            .to_string(),
        ),
        NodeEnum::SqlvalueFunction(f) => sql_value_function_name(f.op).map(|s| s.to_string()),
        NodeEnum::AArrayExpr(_) => Some("array".to_string()),
        NodeEnum::RowExpr(_) => Some("row".to_string()),
        NodeEnum::GroupingFunc(_) => Some("grouping".to_string()),
        NodeEnum::SubLink(l) => match pg_query::protobuf::SubLinkType::try_from(l.sub_link_type) {
            Ok(pg_query::protobuf::SubLinkType::ExistsSublink) => Some("exists".to_string()),
            Ok(pg_query::protobuf::SubLinkType::ArraySublink) => Some("array".to_string()),
            Ok(pg_query::protobuf::SubLinkType::ExprSublink) => l
                .subselect
                .as_ref()
                .and_then(|s| match s.node.as_ref() {
                    Some(NodeEnum::SelectStmt(sel)) => sel.target_list.first(),
                    _ => None,
                })
                .and_then(|t| match t.node.as_ref() {
                    Some(NodeEnum::ResTarget(r)) => {
                        if r.name.is_empty() {
                            r.val.as_ref().and_then(|v| figure_colname(v))
                        } else {
                            Some(r.name.clone())
                        }
                    }
                    _ => None,
                }),
            _ => None,
        },
        NodeEnum::AIndirection(i) => i.arg.as_ref().and_then(|a| figure_colname(a)),
        _ => None,
    }
}

pub fn sql_value_function_name(op: i32) -> Option<&'static str> {
    use pg_query::protobuf::SqlValueFunctionOp as Op;
    Some(match Op::try_from(op).ok()? {
        Op::SvfopCurrentDate => "current_date",
        Op::SvfopCurrentTime | Op::SvfopCurrentTimeN => "current_time",
        Op::SvfopCurrentTimestamp | Op::SvfopCurrentTimestampN => "current_timestamp",
        Op::SvfopLocaltime | Op::SvfopLocaltimeN => "localtime",
        Op::SvfopLocaltimestamp | Op::SvfopLocaltimestampN => "localtimestamp",
        Op::SvfopCurrentRole => "current_role",
        Op::SvfopCurrentUser => "current_user",
        Op::SvfopUser => "user",
        Op::SvfopSessionUser => "session_user",
        Op::SvfopCurrentCatalog => "current_catalog",
        Op::SvfopCurrentSchema => "current_schema",
        Op::SqlvalueFunctionOpUndefined => return None,
    })
}
