//! The expression nullability lattice.
//!
//! Ported from sqlc's `internal/core/analyzer/expr.go` -- the good 742-line
//! design -- with four deliberate corrections, each marked `DEVIATION` below.
//! Everything here answers exactly one question: *can this expression evaluate
//! to NULL for some database state?* Types are not tracked at all; that is what
//! makes the port a third of the size of its source.

use pg_query::protobuf::{
    AExprKind, BoolExprType, MinMaxOp, Node, NullTestType, SubLinkType,
};
use pg_query::NodeEnum;

use crate::catalog::{fold, string_list, variant_name};
use crate::funcs::{AggRule, ScalarRule, WindowRule};
use crate::output::{sql_value_function_name, Analyzer, ExprCtx};
use crate::scope::ScopeStack;

impl Analyzer<'_> {
    pub fn expr_nullable(
        &mut self,
        node: &Node,
        scope: &mut ScopeStack,
        ctx: ExprCtx,
    ) -> Result<bool, String> {
        let Some(inner) = node.node.as_ref() else {
            return Ok(true);
        };
        match inner {
            // -- leaves ----------------------------------------------------
            NodeEnum::AConst(c) => Ok(c.isnull),

            NodeEnum::ColumnRef(cr) => {
                let parts = string_list(&cr.fields);
                match parts.len() {
                    0 => Err("empty column reference".to_string()),
                    1 => self.resolve_column(scope, None, &parts[0]),
                    _ => {
                        // `schema.table.column` and `table.column` both end with
                        // the column and are qualified by the name before it.
                        let col = &parts[parts.len() - 1];
                        let qual = &parts[parts.len() - 2];
                        self.resolve_column(scope, Some(qual), col)
                    }
                }
            }

            // A caller-supplied value. Whether it may be NULL is the caller's
            // business, and this engine cannot see the call site.
            NodeEnum::ParamRef(_) => Ok(true),

            NodeEnum::SqlvalueFunction(f) => {
                let name = sql_value_function_name(f.op).unwrap_or("");
                match self.funcs.scalar(name) {
                    Some(ScalarRule::NeverNull) => Ok(false),
                    _ => {
                        self.warn(format!("SQL value function `{name}` is not in the function \
                                           table; reporting nullable"));
                        Ok(true)
                    }
                }
            }

            NodeEnum::SetToDefault(_) | NodeEnum::CurrentOfExpr(_) => Ok(true),

            // -- operators -------------------------------------------------
            NodeEnum::AExpr(e) => {
                let kind = AExprKind::try_from(e.kind).unwrap_or(AExprKind::Undefined);
                let left = self.opt_expr(&e.lexpr, scope, ctx)?;
                let right = self.opt_expr(&e.rexpr, scope, ctx)?;
                match kind {
                    // `x IS DISTINCT FROM y` is a total function: it is exactly
                    // the predicate that never returns NULL.
                    AExprKind::AexprDistinct | AExprKind::AexprNotDistinct => Ok(false),
                    // NULLIF(a, b) is `a`, made nullable.
                    AExprKind::AexprNullif => Ok(true),
                    // Everything else -- arithmetic, comparison, LIKE, IN,
                    // BETWEEN, ANY/ALL -- is strict in PostgreSQL: NULL on
                    // either side gives NULL, including for the predicates.
                    //
                    // DEVIATION 1 from sqlc: `typePredicateList` and
                    // `typeQuantifiedExpr` (expr.go:302, 318) return
                    // `boolType(false)` for IN / BETWEEN / ANY / ALL. That is
                    // unsound: `NULL IN (1, 2)` is NULL, not false.
                    _ => Ok(left || right),
                }
            }

            // DEVIATION 2 from sqlc: `typeBoolExpr` (expr.go:607-614) returns a
            // non-null boolean for AND / OR / NOT. `NULL AND true` is NULL and
            // `NOT NULL` is NULL, so a bool connective is only non-null when
            // every operand is. Three-valued logic does collapse *some* cases
            // (`NULL AND false` is false), but not enough to claim non-null.
            NodeEnum::BoolExpr(b) => {
                let _ = BoolExprType::try_from(b.boolop);
                self.any_nullable(&b.args, scope, ctx)
            }

            // -- total predicates ------------------------------------------
            NodeEnum::NullTest(t) => {
                let _ = NullTestType::try_from(t.nulltesttype);
                if let Some(arg) = t.arg.as_ref() {
                    self.expr_nullable(arg, scope, ctx)?;
                }
                Ok(false)
            }
            NodeEnum::BooleanTest(t) => {
                if let Some(arg) = t.arg.as_ref() {
                    self.expr_nullable(arg, scope, ctx)?;
                }
                Ok(false)
            }

            // -- structural forms ------------------------------------------
            // COALESCE returns its first non-NULL argument, so it is NULL only
            // when every argument is.
            NodeEnum::CoalesceExpr(e) => self.all_nullable(&e.args, scope, ctx),

            // GREATEST / LEAST skip NULL arguments in PostgreSQL and return
            // NULL only when every argument is NULL -- the same rule as
            // COALESCE.
            //
            // DEVIATION 3 from sqlc: `typeExpr` (expr.go:72) sends MinMaxExpr
            // through `typeFirstOf(..., false)`, which ORs the arguments. Sound,
            // but a needless false positive.
            NodeEnum::MinMaxExpr(e) => {
                let _ = MinMaxOp::try_from(e.op);
                self.all_nullable(&e.args, scope, ctx)
            }

            // CASE is nullable if any branch result is, or if there is no ELSE
            // (an unmatched row yields NULL).
            NodeEnum::CaseExpr(e) => {
                if let Some(arg) = e.arg.as_ref() {
                    self.expr_nullable(arg, scope, ctx)?;
                }
                let mut nullable = e.defresult.is_none();
                for item in &e.args {
                    let Some(NodeEnum::CaseWhen(w)) = item.node.as_ref() else {
                        continue;
                    };
                    if let Some(cond) = w.expr.as_ref() {
                        self.expr_nullable(cond, scope, ctx)?;
                    }
                    if let Some(res) = w.result.as_ref() {
                        nullable |= self.expr_nullable(res, scope, ctx)?;
                    }
                }
                if let Some(d) = e.defresult.as_ref() {
                    nullable |= self.expr_nullable(d, scope, ctx)?;
                }
                Ok(nullable)
            }

            // DEVIATION 4 from sqlc: `typeTypeCast` (expr.go:728-742) returns
            // the target type with `nullable` left at its zero value, i.e.
            // FALSE -- so sqlc reports `nullable_column::text` as NOT NULL.
            // That is unsound. A cast is value-preserving for NULL: NULL::text
            // is NULL.
            NodeEnum::TypeCast(c) => self.opt_expr(&c.arg, scope, ctx),

            NodeEnum::CollateExpr(e) => self.opt_expr(&e.arg, scope, ctx),
            NodeEnum::NamedArgExpr(e) => self.opt_expr(&e.arg, scope, ctx),

            // ARRAY[...] and ROW(...) constructors are never NULL themselves,
            // but the brief's lattice ORs their elements and that is what is
            // implemented -- a safe over-approximation, and the elements' own
            // nullability is what a caller destructuring the row cares about.
            NodeEnum::AArrayExpr(e) => self.any_nullable(&e.elements, scope, ctx),
            NodeEnum::RowExpr(e) => self.any_nullable(&e.args, scope, ctx),

            NodeEnum::List(l) => self.any_nullable(&l.items, scope, ctx),

            // `(x).f` and `arr[i]` -- a subscript out of range is NULL.
            NodeEnum::AIndirection(i) => {
                self.opt_expr(&i.arg, scope, ctx)?;
                Ok(true)
            }

            NodeEnum::GroupingFunc(_) => Ok(false),

            // -- subqueries -------------------------------------------------
            NodeEnum::SubLink(l) => {
                if let Some(t) = l.testexpr.as_ref() {
                    self.expr_nullable(t, scope, ctx)?;
                }
                let kind = SubLinkType::try_from(l.sub_link_type).unwrap_or(SubLinkType::Undefined);
                // The subquery is analysed either way: it may reference outer
                // relations, and any error inside it is a real error.
                if let Some(sub) = l.subselect.as_ref() {
                    scope.push_frame();
                    let r = self.analyze_query_node(sub, scope);
                    scope.pop_frame();
                    r?;
                }
                match kind {
                    // EXISTS is total: it is true or false, never NULL.
                    SubLinkType::ExistsSublink => Ok(false),
                    // A scalar subquery that matches no row yields NULL, and
                    // ANY/ALL over a set containing NULL yields NULL.
                    _ => Ok(true),
                }
            }

            // -- calls -------------------------------------------------------
            NodeEnum::FuncCall(f) => self.func_call_nullable(f, scope, ctx),

            other => {
                self.warn(format!(
                    "expression node {} is not modelled; reporting nullable",
                    variant_name(other)
                ));
                Ok(true)
            }
        }
    }

    fn func_call_nullable(
        &mut self,
        f: &pg_query::protobuf::FuncCall,
        scope: &mut ScopeStack,
        ctx: ExprCtx,
    ) -> Result<bool, String> {
        let name = string_list(&f.funcname)
            .last()
            .map(|s| fold(s))
            .unwrap_or_default();

        // Argument nullability is computed first: even a `never_null` function
        // must have its arguments walked, because an argument may itself be a
        // subquery or an unresolvable column reference.
        let args_nullable = self.any_nullable(&f.args, scope, ctx)?;
        if let Some(filter) = f.agg_filter.as_ref() {
            self.expr_nullable(filter, scope, ctx)?;
        }

        // -- window function -------------------------------------------------
        if f.over.is_some() {
            if let Some(rule) = self.funcs.window(&name) {
                return Ok(match rule {
                    WindowRule::NeverNull => false,
                    WindowRule::Nullable => true,
                });
            }
            // An aggregate used as a window function: the frame may be empty for
            // some row, so only COUNT survives.
            return Ok(!matches!(
                self.funcs.aggregate(&name),
                Some(AggRule::NeverNull)
            ));
        }

        // -- aggregate -------------------------------------------------------
        if let Some(rule) = self.funcs.aggregate(&name) {
            return Ok(match rule {
                AggRule::NeverNull => false,
                AggRule::Grouped => {
                    if f.agg_filter.is_some() {
                        // FILTER can empty a group, and an aggregate over zero
                        // rows is NULL.
                        true
                    } else if ctx.grouped {
                        // One row per non-empty group, so the only way to get a
                        // NULL out is to put one in.
                        args_nullable
                    } else {
                        // A whole-table aggregate emits one row even over an
                        // empty table, and SUM of nothing is NULL.
                        true
                    }
                }
            });
        }

        // -- ordinary function ------------------------------------------------
        match self.funcs.scalar(&name) {
            Some(ScalarRule::NeverNull) => Ok(false),
            Some(ScalarRule::Strict) => Ok(args_nullable),
            Some(ScalarRule::Nullable) => Ok(true),
            None => {
                self.warn(format!(
                    "function `{name}` is not in the nullability table; reporting nullable"
                ));
                Ok(true)
            }
        }
    }

    fn opt_expr(
        &mut self,
        node: &Option<Box<Node>>,
        scope: &mut ScopeStack,
        ctx: ExprCtx,
    ) -> Result<bool, String> {
        match node {
            Some(n) => self.expr_nullable(n, scope, ctx),
            None => Ok(false),
        }
    }

    /// OR over a list. Every element is walked even once the answer is known,
    /// so that errors and warnings inside later elements are still reported.
    fn any_nullable(
        &mut self,
        nodes: &[Node],
        scope: &mut ScopeStack,
        ctx: ExprCtx,
    ) -> Result<bool, String> {
        let mut nullable = false;
        for n in nodes {
            nullable |= self.expr_nullable(n, scope, ctx)?;
        }
        Ok(nullable)
    }

    /// AND over a list: NULL only when every element can be. An empty list is
    /// nullable, which only arises for a malformed tree.
    fn all_nullable(
        &mut self,
        nodes: &[Node],
        scope: &mut ScopeStack,
        ctx: ExprCtx,
    ) -> Result<bool, String> {
        if nodes.is_empty() {
            return Ok(true);
        }
        let mut nullable = true;
        for n in nodes {
            nullable &= self.expr_nullable(n, scope, ctx)?;
        }
        Ok(nullable)
    }
}
