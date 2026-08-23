# M4 evidence index

Every gate, what proves it, and where the raw output lives.

| gate | verdict | evidence |
|---|---|---|
| **R1** build (native + musl static, smoke-run in alpine) | PASS | `build_native.log`, `build_musl.log`, `smoke_alpine.log` |
| **R2** corpus parity over the full M3 corpus | PASS — 26 cases / 65 columns, **65 PASS, 0 safe-FP, 0 UNSOUND, 0 mismatch, 0 error** (Phase 1: 8 safe-FP) | `scores_rust_m3corpus.txt` / `.json`, `scores_phase1_m3corpus.txt` / `.json`, `warnings_rust_m3corpus.txt` |
| **R3** sqlc superiority on the three bug cases | PASS — all three exactly correct; ledger written | `divergence_ledger.md`, `scores_rust_m3corpus.txt` |
| **R4** the Phase-2 delta demonstrated | PASS — 11 new cases / 29 columns, Rust **29 PASS / 0 safe-FP**, Phase 1 **17 PASS / 12 safe-FP** | `scores_rust_corpusext.txt`, `scores_phase1_corpusext.txt`, `phase1_vs_phase2.md` |
| **R5** live-PostgreSQL oracle over the Rust engine's claims | PASS — 32 seeded cases, 74 result rows, 105 NOT NULL assertions, **0 violations, 0 errors** | `oracle_rust_m3corpus.txt`, `oracle_rust_corpusext.txt` |
| **R6** determinism + perf | PASS — 37 cases × 5 runs byte-identical; analysis 0.065 ms, catalog 0.115 ms | `determinism_and_perf.txt` |
| (supporting) DDL coverage | 7/7 EC-schema tables loaded with **zero** warnings; unsupported constructs warn, never crash | `ddl_support.txt` |
| (supporting) edge behaviour | errors / ambiguity / unknown functions / FILTER / OVER / ROLLUP / RECURSIVE / RETURNING | `edge_cases.txt` |
| (supporting) size | 2,539 lines of Rust + embedded data, against a 2,500–3,500 estimate | `loc.txt` |

## Headline numbers

```
                       cases  columns   PASS  safe-FP  UNSOUND  mismatch  error
M3 corpus
  altsa_sqlgen-phase1     26       65     57        8        0         0      0
  altsa-analyze (M4)      26       65     65        0        0         0      0
corpus-ext
  altsa_sqlgen-phase1     11       29     17       12        0         0      0
  altsa-analyze (M4)      11       29     29        0        0         0      0
combined (M4)             37       94     94        0        0         0      0
```

## Build facts

| | native | musl static |
|---|---|---|
| toolchain | cargo 1.98.0 / rustc 1.98.0, Darwin arm64 | `messense/rust-musl-cross:x86_64-musl` |
| clean build | 23.8 s | 1 m 27 s (incl. docker start) |
| no-op rebuild | 0.06 s | 6.6 s |
| binary | 4,571,568 B, Mach-O arm64 | 4,974,416 B, ELF x86-64 static-pie |
| runs on | macOS | alpine 3.20.10 (`ldd`: musl loader only) |

### Perf

`best-of-20` **process** wall time over all 37 cases: min 2.05 ms, median
2.16 ms, max 2.31 ms. That is dominated by `fork`/`exec` and dynamic loading —
the binary's own instrumentation (`--timing`) reports, for the slowest case,
**0.115 ms to parse and load the DDL** and **0.065 ms to analyse the query**.
So the analysis itself is well under a millisecond; a caller that loaded the
catalog once and analysed many queries would pay ~65 µs each.

## Reproducing

```sh
sh harness/build_native.sh          > evidence/build_native.log 2>&1
sh harness/build_musl_clean.sh      > evidence/build_musl.log   2>&1
sh harness/smoke_alpine.sh          > evidence/smoke_alpine.log 2>&1
sh harness/collect_evidence.sh "$DSN"
sh harness/run_r6.sh
sh harness/run_ddl_probe.sh         > evidence/ddl_support.txt  2>&1
sh harness/run_edge_cases.sh        > evidence/edge_cases.txt   2>&1
python3 harness/delta_table.py      > evidence/phase1_vs_phase2.md
python3 harness/loc.py              > evidence/loc.txt
```

`$DSN` pointed at a throwaway `postgres:16-alpine` container named
`altsa-m4-pg` on port 55437, removed at the end of the run. Only the Phase 1
runs and the oracle need it; the Rust engine's own scores are produced with no
database in the environment at all.
