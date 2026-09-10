# trace-agent — clinical data traceability checker

[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![PyPI](https://img.shields.io/badge/pypi-cdisc--trace--agent-orange.svg)](https://pypi.org/project/cdisc-trace-agent/)

**Free, open-source tool that finds traceability gaps in CDISC submissions before the FDA does.**

Given a study's CDISC `define.xml` (2.0/2.1) and its SAS transport (XPT)
datasets, `trace-agent` builds a **variable-level lineage graph**
(ADaM ← SDTM ← aCRF/protocol) and flags **traceability gaps** — the #1
source of findings in regulatory review.

🚀 **[Try the live demo in your browser](https://muse.ai/s/trace-agent-xcxz5y1xossxxxao)** — no install needed.

## Install

```bash
pip install cdisc-trace-agent
```

Or from source:

```bash
git clone https://github.com/girishkankipati2-pixel/trace-agent.git
cd trace-agent
pip install -e .
```

## Run

```bash
trace-agent --study <study-folder> --out report.html
```

The study folder must contain `define.xml` at its root and `*.xpt` files
at the root or in an `xpt/` subdirectory. A text summary is printed and a
self-contained HTML report (inline SVG + JS, no external dependencies) is
written to `--out`. The report has summary counts, a gap table, and a
clickable lineage graph (click a node to highlight its lineage).

## Demo

```bash
python make_demo.py                                  # builds demo_study/
python trace_agent.py --study demo_study --out demo_report.html
```

`demo_study/` is a tiny synthetic study (SDTM DM + AE, ADaM ADSL) with five
deliberately planted gaps — see `demo_study/planted_gaps.txt`. The demo run
should report all of them.

## Gaps detected

| Code | Meaning |
|---|---|
| `XPT_ONLY` | Variable present in XPT but missing from define.xml |
| `DEFINE_ONLY` | Variable defined in define.xml but missing from XPT |
| `DERIVED_NO_METHOD` | ADaM variable with Origin=`Derived` but no MethodDef/description |
| `DANGLING_REF` | Derivation references a variable defined nowhere |
| `NO_SDTM_PATH` | ADaM variable with no predecessor chain reaching SDTM |

## How lineage is resolved

For each variable, predecessors are resolved from, in order:

1. The linked `def:MethodDef` (via `ItemRef/@MethodOID`, per the
   Define-XML 2.0 spec) — description and formal-expression text are scanned
   for `DOMAIN.VAR` references.
2. Inline `def:Origin` description text (same reference scanning).
3. `def:Origin Type="Predecessor"` — links to the same-named variable in
   SDTM datasets.

Explicit dotted references (`DM.AGE`) are authoritative; bare tokens only
count when they uniquely match one known variable name.

## Limitations (v1)

- Annotated CRF / protocol documents are not parsed; CRF origins are
  recorded but not linked to pages.
- `def:DocumentRef`, value-level metadata (`ValueListDef`), and ARM
  (`arm:AnalysisResult`) are not yet followed.
- Dataset role (SDTM vs ADaM) uses `def:Class`, falling back to an `AD*`
  name heuristic.
- Reference extraction from derivation prose is heuristic text matching,
  not SAS parsing.
