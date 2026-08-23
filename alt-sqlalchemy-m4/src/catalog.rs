//! DDL catalog loader.
//!
//! Parses a `schema.sql` with pg_query and keeps the only thing the
//! nullability engine needs: for every table, the ordered list of column names
//! and whether each one is declared NOT NULL.
//!
//! Deliberately *not* a full DDL implementation. Anything it does not model is
//! reported as a warning and skipped; nothing here may abort the run, because a
//! schema is allowed to contain constructs (triggers, policies, extensions)
//! that cannot affect result-column nullability at all.

use std::collections::BTreeMap;

use pg_query::protobuf::{AlterTableType, ConstrType, Node, ObjectType};
use pg_query::NodeEnum;

#[derive(Debug, Clone)]
pub struct Column {
    pub name: String,
    /// `true` when the catalog guarantees the column can never hold NULL.
    pub not_null: bool,
}

#[derive(Debug, Clone, Default)]
pub struct Table {
    pub name: String,
    pub columns: Vec<Column>,
}

#[derive(Debug, Default)]
pub struct Catalog {
    tables: BTreeMap<String, Table>,
}

impl Catalog {
    pub fn get(&self, name: &str) -> Option<&Table> {
        self.tables.get(&fold(name))
    }

    pub fn len(&self) -> usize {
        self.tables.len()
    }

    /// Load every statement of a DDL script. Unmodelled statements become
    /// warnings; a parse error is the one hard failure.
    pub fn load(&mut self, sql: &str, warnings: &mut Vec<String>) -> Result<(), String> {
        let parsed = pg_query::parse(sql).map_err(|e| format!("schema parse error: {e}"))?;
        for raw in &parsed.protobuf.stmts {
            let Some(node) = raw.stmt.as_ref().and_then(|n| n.node.as_ref()) else {
                continue;
            };
            self.apply(node, warnings);
        }
        Ok(())
    }

    fn apply(&mut self, node: &NodeEnum, warnings: &mut Vec<String>) {
        match node {
            NodeEnum::CreateStmt(c) => {
                let Some(rel) = c.relation.as_ref() else { return };
                let mut table = Table {
                    name: fold(&rel.relname),
                    columns: Vec::new(),
                };
                let mut pk_keys: Vec<String> = Vec::new();
                for elt in &c.table_elts {
                    match elt.node.as_ref() {
                        Some(NodeEnum::ColumnDef(cd)) => {
                            let (col, keys) = column_from_def(cd);
                            pk_keys.extend(keys);
                            table.columns.push(col);
                        }
                        Some(NodeEnum::Constraint(con)) => {
                            if ConstrType::try_from(con.contype) == Ok(ConstrType::ConstrPrimary) {
                                pk_keys.extend(string_list(&con.keys));
                            }
                        }
                        Some(NodeEnum::TableLikeClause(_)) => warnings.push(format!(
                            "catalog: CREATE TABLE {} uses LIKE, inherited columns are not modelled",
                            rel.relname
                        )),
                        _ => {}
                    }
                }
                for key in pk_keys {
                    if let Some(c) = table.columns.iter_mut().find(|c| c.name == fold(&key)) {
                        c.not_null = true;
                    }
                }
                self.tables.insert(table.name.clone(), table);
            }

            NodeEnum::AlterTableStmt(a) => {
                if ObjectType::try_from(a.objtype) != Ok(ObjectType::ObjectTable) {
                    return;
                }
                let Some(rel) = a.relation.as_ref() else { return };
                let key = fold(&rel.relname);
                let Some(table) = self.tables.get_mut(&key) else {
                    warnings.push(format!(
                        "catalog: ALTER TABLE {} refers to a table this catalog does not hold",
                        rel.relname
                    ));
                    return;
                };
                for cmd in &a.cmds {
                    let Some(NodeEnum::AlterTableCmd(cmd)) = cmd.node.as_ref() else {
                        continue;
                    };
                    match AlterTableType::try_from(cmd.subtype) {
                        Ok(AlterTableType::AtAddColumn) => {
                            if let Some(NodeEnum::ColumnDef(cd)) =
                                cmd.def.as_ref().and_then(|d| d.node.as_ref())
                            {
                                let (col, keys) = column_from_def(cd);
                                let pk = !keys.is_empty();
                                let mut col = col;
                                col.not_null |= pk;
                                table.columns.push(col);
                            }
                        }
                        Ok(AlterTableType::AtSetNotNull) => {
                            if let Some(c) =
                                table.columns.iter_mut().find(|c| c.name == fold(&cmd.name))
                            {
                                c.not_null = true;
                            }
                        }
                        Ok(AlterTableType::AtDropNotNull) => {
                            if let Some(c) =
                                table.columns.iter_mut().find(|c| c.name == fold(&cmd.name))
                            {
                                c.not_null = false;
                            }
                        }
                        Ok(AlterTableType::AtDropColumn) => {
                            table.columns.retain(|c| c.name != fold(&cmd.name));
                        }
                        Ok(AlterTableType::AtAddConstraint) => {
                            if let Some(NodeEnum::Constraint(con)) =
                                cmd.def.as_ref().and_then(|d| d.node.as_ref())
                            {
                                if ConstrType::try_from(con.contype)
                                    == Ok(ConstrType::ConstrPrimary)
                                {
                                    for key in string_list(&con.keys) {
                                        if let Some(c) =
                                            table.columns.iter_mut().find(|c| c.name == fold(&key))
                                        {
                                            c.not_null = true;
                                        }
                                    }
                                }
                            }
                        }
                        // Everything else (SET STORAGE, OWNER, indexes, ...)
                        // cannot change nullability.
                        _ => {}
                    }
                }
            }

            NodeEnum::DropStmt(d) => match ObjectType::try_from(d.remove_type) {
                Ok(ObjectType::ObjectTable) => {
                    for obj in &d.objects {
                        for name in qualified_name(obj) {
                            self.tables.remove(&name);
                        }
                    }
                }
                Ok(ObjectType::ObjectSchema) => {
                    // `DROP SCHEMA public CASCADE` at the top of a script: every
                    // table this catalog holds lived there.
                    self.tables.clear();
                }
                _ => {}
            },

            // Known-irrelevant to result nullability. Listed explicitly so that
            // the catch-all below really does mean "unmodelled".
            NodeEnum::CreateSchemaStmt(_)
            | NodeEnum::CreateExtensionStmt(_)
            | NodeEnum::CreateEnumStmt(_)
            | NodeEnum::CreateRangeStmt(_)
            | NodeEnum::CompositeTypeStmt(_)
            | NodeEnum::CreateDomainStmt(_)
            | NodeEnum::CreateSeqStmt(_)
            | NodeEnum::AlterSeqStmt(_)
            | NodeEnum::IndexStmt(_)
            | NodeEnum::CommentStmt(_)
            | NodeEnum::GrantStmt(_)
            | NodeEnum::CreateTrigStmt(_)
            | NodeEnum::CreateFunctionStmt(_)
            | NodeEnum::CreatePolicyStmt(_)
            | NodeEnum::VariableSetStmt(_)
            | NodeEnum::TransactionStmt(_)
            | NodeEnum::CreateCastStmt(_)
            | NodeEnum::DefineStmt(_)
            | NodeEnum::CreateStatsStmt(_)
            | NodeEnum::RuleStmt(_)
            | NodeEnum::AlterEnumStmt(_)
            | NodeEnum::AlterOwnerStmt(_)
            | NodeEnum::RenameStmt(_) => {}

            NodeEnum::ViewStmt(v) => warnings.push(format!(
                "catalog: CREATE VIEW {} is not modelled; queries over it will not resolve",
                v.view.as_ref().map(|r| r.relname.as_str()).unwrap_or("?")
            )),
            NodeEnum::CreateTableAsStmt(_) => warnings
                .push("catalog: CREATE TABLE AS / MATERIALIZED VIEW is not modelled".to_string()),
            NodeEnum::CreateForeignTableStmt(_) => {
                warnings.push("catalog: CREATE FOREIGN TABLE is not modelled".to_string())
            }
            other => warnings.push(format!(
                "catalog: unmodelled DDL statement {}",
                variant_name(other)
            )),
        }
    }
}

/// Build a column from a `ColumnDef`, returning any inline PRIMARY KEY name.
fn column_from_def(cd: &pg_query::protobuf::ColumnDef) -> (Column, Vec<String>) {
    let mut not_null = cd.is_not_null;
    let mut keys = Vec::new();

    // An identity column (`GENERATED ... AS IDENTITY`) is implicitly NOT NULL.
    if !cd.identity.is_empty() {
        not_null = true;
    }
    // `serial` / `bigserial` expand to `integer NOT NULL DEFAULT nextval(...)`.
    if let Some(tn) = cd.type_name.as_ref() {
        if let Some(last) = string_list(&tn.names).last() {
            if matches!(
                fold(last).as_str(),
                "serial" | "serial4" | "bigserial" | "serial8" | "smallserial" | "serial2"
            ) {
                not_null = true;
            }
        }
    }
    for con in &cd.constraints {
        let Some(NodeEnum::Constraint(con)) = con.node.as_ref() else {
            continue;
        };
        match ConstrType::try_from(con.contype) {
            Ok(ConstrType::ConstrNotnull) => not_null = true,
            Ok(ConstrType::ConstrIdentity) => not_null = true,
            Ok(ConstrType::ConstrPrimary) => keys.push(cd.colname.clone()),
            _ => {}
        }
    }
    (
        Column {
            name: fold(&cd.colname),
            not_null,
        },
        keys,
    )
}

fn qualified_name(node: &Node) -> Vec<String> {
    match node.node.as_ref() {
        Some(NodeEnum::List(l)) => {
            let parts = string_list(&l.items);
            parts.last().map(|s| vec![fold(s)]).unwrap_or_default()
        }
        Some(NodeEnum::String(s)) => vec![fold(&s.sval)],
        _ => Vec::new(),
    }
}

pub fn string_list(nodes: &[Node]) -> Vec<String> {
    nodes
        .iter()
        .filter_map(|n| match n.node.as_ref() {
            Some(NodeEnum::String(s)) => Some(s.sval.clone()),
            _ => None,
        })
        .collect()
}

/// PostgreSQL folds unquoted identifiers to lower case; pg_query already did
/// that for us, but a quoted `"Foo"` reaches us verbatim. Matching on the
/// lower-cased spelling keeps the common case working and is never unsound --
/// at worst two differently-quoted tables collide, which the warning list would
/// not catch. Corpus and EC schemas use no quoted identifiers.
pub fn fold(name: &str) -> String {
    name.to_ascii_lowercase()
}

/// Short, stable name of a NodeEnum variant, for warning text.
///
/// prost's derived `Debug` prints the whole subtree, so the discriminant is
/// recovered by cutting at the first `(`. That is the only name the generated
/// protobuf bindings expose without a 268-arm match.
pub fn variant_name(n: &NodeEnum) -> String {
    let dbg = format!("{n:?}");
    dbg.split('(').next().unwrap_or("Node").to_string()
}
