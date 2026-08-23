//! The function nullability table, read from the embedded `functions.toml`.

use std::collections::BTreeMap;

use serde::Deserialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScalarRule {
    /// NULL in, NULL out.
    Strict,
    /// Never NULL, whatever the arguments.
    NeverNull,
    /// May be NULL even when every argument is non-NULL.
    Nullable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AggRule {
    /// NULL is impossible even over zero rows (COUNT).
    NeverNull,
    /// One row per non-empty group under GROUP BY (so: argument nullability);
    /// a single row summarising possibly-zero rows without one (so: nullable).
    Grouped,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WindowRule {
    NeverNull,
    Nullable,
}

#[derive(Debug, Deserialize)]
struct RawTable {
    #[serde(default)]
    scalar: BTreeMap<String, String>,
    #[serde(default)]
    aggregate: BTreeMap<String, String>,
    #[serde(default)]
    window: BTreeMap<String, String>,
}

#[derive(Debug)]
pub struct FuncTable {
    scalar: BTreeMap<String, ScalarRule>,
    aggregate: BTreeMap<String, AggRule>,
    window: BTreeMap<String, WindowRule>,
}

const EMBEDDED: &str = include_str!("functions.toml");

impl FuncTable {
    pub fn load() -> Result<Self, String> {
        Self::parse(EMBEDDED)
    }

    pub fn parse(text: &str) -> Result<Self, String> {
        let raw: RawTable =
            toml::from_str(text).map_err(|e| format!("functions.toml is not valid: {e}"))?;
        let mut scalar = BTreeMap::new();
        for (name, rule) in raw.scalar {
            let r = match rule.as_str() {
                "strict" => ScalarRule::Strict,
                "never_null" => ScalarRule::NeverNull,
                "nullable" => ScalarRule::Nullable,
                other => return Err(format!("functions.toml: scalar {name}: unknown rule {other}")),
            };
            scalar.insert(name, r);
        }
        let mut aggregate = BTreeMap::new();
        for (name, rule) in raw.aggregate {
            let r = match rule.as_str() {
                "never_null" => AggRule::NeverNull,
                "grouped" => AggRule::Grouped,
                other => {
                    return Err(format!(
                        "functions.toml: aggregate {name}: unknown rule {other}"
                    ))
                }
            };
            aggregate.insert(name, r);
        }
        let mut window = BTreeMap::new();
        for (name, rule) in raw.window {
            let r = match rule.as_str() {
                "never_null" => WindowRule::NeverNull,
                "nullable" => WindowRule::Nullable,
                other => return Err(format!("functions.toml: window {name}: unknown rule {other}")),
            };
            window.insert(name, r);
        }
        Ok(Self {
            scalar,
            aggregate,
            window,
        })
    }

    pub fn scalar(&self, name: &str) -> Option<ScalarRule> {
        self.scalar.get(name).copied()
    }

    pub fn aggregate(&self, name: &str) -> Option<AggRule> {
        self.aggregate.get(name).copied()
    }

    pub fn window(&self, name: &str) -> Option<WindowRule> {
        self.window.get(name).copied()
    }

    pub fn len(&self) -> usize {
        self.scalar.len() + self.aggregate.len() + self.window.len()
    }
}
