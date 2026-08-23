//! `${name}` parameter markers.
//!
//! A direct port of `altsa_sqlgen.annotations.scan_param_refs` (M3), so that
//! `altsa-analyze` eats a corpus `query.sql` verbatim instead of needing the
//! Python harness to rewrite it first. The only thing this understands about
//! SQL is where its string literals, comments and quoted identifiers are,
//! because a `${...}` inside one of those is not a parameter.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Param {
    pub name: String,
    /// 1-based, in first-occurrence order -- the `$n` the rewritten SQL uses.
    pub index: usize,
    /// Spelled `${name?}`: the caller may pass NULL.
    pub nullable: bool,
}

#[derive(Debug)]
pub struct Rewritten {
    /// SQL with every `${name}` replaced by `$n`, ready for pg_query.
    pub sql: String,
    pub params: Vec<Param>,
}

/// Replace `${name}` / `${name?}` with `$n`, numbering by first occurrence.
pub fn rewrite(sql: &str) -> Result<Rewritten, String> {
    let b = sql.as_bytes();
    let n = b.len();
    let mut out = String::with_capacity(n);
    let mut params: Vec<Param> = Vec::new();
    let mut i = 0usize;

    while i < n {
        let c = b[i];

        // -- line comment
        if c == b'-' && b[i..].starts_with(b"--") {
            let end = match sql[i..].find('\n') {
                Some(j) => i + j + 1,
                None => n,
            };
            out.push_str(&sql[i..end]);
            i = end;
            continue;
        }

        // /* nesting block comment */
        if c == b'/' && b[i..].starts_with(b"/*") {
            let start = i;
            let mut depth = 1usize;
            i += 2;
            while i < n && depth > 0 {
                if b[i..].starts_with(b"/*") {
                    depth += 1;
                    i += 2;
                } else if b[i..].starts_with(b"*/") {
                    depth -= 1;
                    i += 2;
                } else {
                    i += 1;
                }
            }
            out.push_str(&sql[start..i]);
            continue;
        }

        // E'...' escape string (backslash escapes)
        if (c == b'e' || c == b'E') && i + 1 < n && b[i + 1] == b'\'' && !is_ident_byte_before(b, i)
        {
            let start = i;
            i += 2;
            while i < n {
                if b[i] == b'\\' {
                    i += 2;
                } else if b[i] == b'\'' {
                    if b[i..].starts_with(b"''") {
                        i += 2;
                    } else {
                        i += 1;
                        break;
                    }
                } else {
                    i += 1;
                }
            }
            out.push_str(&sql[start..i.min(n)]);
            continue;
        }

        // '...' string literal
        if c == b'\'' {
            let start = i;
            i += 1;
            while i < n {
                if b[i] == b'\'' {
                    if b[i..].starts_with(b"''") {
                        i += 2;
                    } else {
                        i += 1;
                        break;
                    }
                } else {
                    i += 1;
                }
            }
            out.push_str(&sql[start..i.min(n)]);
            continue;
        }

        // "..." quoted identifier -- this is where `AS "col!"` markers live and
        // they must never be touched.
        if c == b'"' {
            let start = i;
            i += 1;
            while i < n {
                if b[i] == b'"' {
                    if b[i..].starts_with(b"\"\"") {
                        i += 2;
                    } else {
                        i += 1;
                        break;
                    }
                } else {
                    i += 1;
                }
            }
            out.push_str(&sql[start..i.min(n)]);
            continue;
        }

        // ${var} / ${var?} -- checked BEFORE dollar-quoting, since a `$tag$`
        // cannot start with `{`.
        if c == b'$' && i + 1 < n && b[i + 1] == b'{' {
            let Some(rel) = sql[i..].find('}') else {
                return Err(format!("unterminated ${{...}} at offset {i}"));
            };
            let close = i + rel;
            let body = &sql[i + 2..close];
            let nullable = body.ends_with('?');
            let name = if nullable {
                &body[..body.len() - 1]
            } else {
                body
            };
            if !is_ident(name) {
                return Err(format!(
                    "invalid parameter name ${{{body}}} -- expected an identifier, \
                     optionally followed by '?'"
                ));
            }
            let index = match params.iter().find(|p| p.name == name) {
                Some(p) => p.index,
                None => {
                    let idx = params.len() + 1;
                    params.push(Param {
                        name: name.to_string(),
                        index: idx,
                        nullable,
                    });
                    idx
                }
            };
            out.push('$');
            out.push_str(&index.to_string());
            i = close + 1;
            continue;
        }

        // $tag$ dollar-quoted string
        if c == b'$' {
            if let Some(tag_len) = dollar_tag_len(b, i) {
                let tag = &sql[i..i + tag_len];
                let start = i;
                i = match sql[i + tag_len..].find(tag) {
                    Some(j) => i + tag_len + j + tag_len,
                    None => n,
                };
                out.push_str(&sql[start..i]);
                continue;
            }
        }

        // Ordinary byte. Copy the whole UTF-8 char.
        let ch_len = utf8_len(c);
        out.push_str(&sql[i..(i + ch_len).min(n)]);
        i += ch_len;
    }

    Ok(Rewritten { sql: out, params })
}

fn utf8_len(b: u8) -> usize {
    match b {
        0x00..=0x7f => 1,
        0xc0..=0xdf => 2,
        0xe0..=0xef => 3,
        _ => 4,
    }
}

fn is_ident(s: &str) -> bool {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn is_ident_byte_before(b: &[u8], i: usize) -> bool {
    i > 0 && (b[i - 1].is_ascii_alphanumeric() || b[i - 1] == b'_')
}

/// Length of a `$tag$` opener at `i`, if there is one.
fn dollar_tag_len(b: &[u8], i: usize) -> Option<usize> {
    let mut j = i + 1;
    if j < b.len() && (b[j].is_ascii_alphabetic() || b[j] == b'_') {
        j += 1;
        while j < b.len() && (b[j].is_ascii_alphanumeric() || b[j] == b'_') {
            j += 1;
        }
    }
    if j < b.len() && b[j] == b'$' {
        Some(j + 1 - i)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repeats_collapse_to_one_index() {
        let r = rewrite("SELECT a FROM t WHERE ${x}::int IS NULL OR a >= ${x}").unwrap();
        assert_eq!(r.sql, "SELECT a FROM t WHERE $1::int IS NULL OR a >= $1");
        assert_eq!(r.params.len(), 1);
        assert_eq!(r.params[0].index, 1);
    }

    #[test]
    fn markers_inside_literals_are_left_alone() {
        let r = rewrite("SELECT '${nope}' AS s, \"col?\" FROM t WHERE b = ${yes}").unwrap();
        assert_eq!(
            r.sql,
            "SELECT '${nope}' AS s, \"col?\" FROM t WHERE b = $1"
        );
        assert_eq!(r.params.len(), 1);
        assert_eq!(r.params[0].name, "yes");
    }

    #[test]
    fn nullable_marker_is_recorded() {
        let r = rewrite("SELECT ${a?}").unwrap();
        assert!(r.params[0].nullable);
        assert_eq!(r.sql, "SELECT $1");
    }
}
