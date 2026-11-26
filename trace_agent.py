#!/usr/bin/env python3
"""
trace.py -- Clinical data traceability agent (v1.1.0).

Given a study folder containing a CDISC define.xml (2.0/2.1) and SAS
transport (XPT) datasets, this tool:

  1. Parses define.xml for datasets (ItemGroupDef), variables (ItemDef,
     incl. def:Origin), derivations (def:MethodDef) and the ItemRef ->
     MethodDef linkage (ItemRef/@MethodOID, per the Define-XML 2.0 spec).
  2. Reads the XPT datasets and compares them against define.xml.
  3. Builds a variable-level lineage graph (ADaM <- SDTM <- raw source).
  4. Flags traceability gaps, including raw-leg gaps: a CRF origin with
     no annotated-CRF link, a DocumentRef pointing at a missing document,
     an eDT origin with no source named, and an undocumented SDTM derivation.
  5. Writes a single self-contained HTML report (inline SVG + JS).

Usage:
    python trace.py --study <folder> --out report.html

Study folder layout expected:
    <folder>/define.xml          (CDISC Define-XML 2.0 or 2.1)
    <folder>/*.xpt               (SAS transport files, e.g. dm.xpt)
    <folder>/xpt/*.xpt           (also searched)
"""

import argparse
import html as html_lib
import json
import os
import re
import sys
from collections import deque

try:
    from lxml import etree
except ImportError:
    sys.exit("ERROR: lxml is required. Run: pip install -r requirements.txt")

try:
    import pyreadstat
except ImportError:
    sys.exit("ERROR: pyreadstat is required. Run: pip install -r requirements.txt")


# ---------------------------------------------------------------------------
# XML helpers (namespace-agnostic so both define 2.0 and 2.1 parse the same)
# ---------------------------------------------------------------------------

DEF_NS_URIS = (
    "http://www.cdisc.org/ns/def/v2.0",
    "http://www.cdisc.org/ns/def/v2.1",
)
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


def local(tag):
    """Strip any namespace, returning the local element name."""
    if not isinstance(tag, str):  # comments/PIs have non-string tags
        return ""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def children(el, name):
    return [c for c in el if local(c.tag) == name]


def def_attr(el, name):
    """Read a define-extension attribute (def:Name) regardless of 2.0/2.1."""
    for uri in DEF_NS_URIS:
        v = el.get("{%s}%s" % (uri, name))
        if v is not None:
            return v
    return None


def desc_text(el):
    """Text of <Description><TranslatedText>...</TranslatedText></Description>."""
    if el is None:
        return ""
    d = child(el, "Description")
    if d is None:
        return ""
    t = child(d, "TranslatedText")
    target = t if t is not None else d
    return "".join(target.itertext()).strip()


def formal_expression_text(method_el):
    parts = []
    for fe in method_el.iter():
        if local(fe.tag) == "FormalExpression":
            ctx = fe.get("Context") or ""
            body = "".join(fe.itertext()).strip()
            parts.append(("[%s] %s" % (ctx, body)) if ctx else body)
    return " ".join(parts).strip()


# ---------------------------------------------------------------------------
# define.xml parsing
# ---------------------------------------------------------------------------

def origin_docrefs(origin_el):
    """DocumentRef children of a def:Origin element.

    Each entry is {leaf_id, page, has_page}. A page reference counts as
    present when the DocumentRef carries a PageRefs/PageRef attribute or a
    PDFPageRef child naming pages (the 2.1 form)."""
    refs = []
    if origin_el is None:
        return refs
    for dr in children(origin_el, "DocumentRef"):
        leaf_id = dr.get("leafID") or ""
        page = dr.get("PageRefs") or dr.get("PageRef") or ""
        if not page:
            for pr in children(dr, "PDFPageRef"):
                page = (pr.get("PageRefs") or pr.get("FirstPage")
                        or pr.get("PageRef") or "")
                if page:
                    break
        refs.append({"leaf_id": leaf_id, "page": page,
                     "has_page": bool(page)})
    return refs


def parse_define(path):
    """Parse define.xml -> (datasets, variables, methods).

    datasets:  {DSNAME: {name, label, domain, role, xpt, var_order:[VARNAME]}}
    variables: {"DS.VAR": {dataset, name, label, dtype, length, origin_type,
                           origin_desc, docrefs, method_oid}}
    methods:   {OID: {oid, name, type, description, formal}}
    leaves:    {leafID: href} for every def:leaf in the file.
    role is 'ADAM' or 'SDTM' (def:Class wins; otherwise AD* name heuristic).
    """
    tree = etree.parse(path)
    root = tree.getroot()

    mdv = None
    for el in root.iter():
        if local(el.tag) == "MetaDataVersion":
            mdv = el
            break
    if mdv is None:
        raise ValueError("No MetaDataVersion element found in %s" % path)

    # def:leaf ID -> href (maps datasets to their XPT files)
    leaves = {}
    for el in mdv.iter():
        if local(el.tag) == "leaf":
            lid = el.get("ID")
            href = el.get(XLINK_HREF)
            if lid and href:
                leaves[lid] = href

    # def:MethodDef elements: the documented derivation algorithms.
    methods = {}
    for el in mdv.iter():
        if local(el.tag) == "MethodDef":
            oid = el.get("OID")
            if not oid:
                continue
            methods[oid] = {
                "oid": oid,
                "name": el.get("Name") or "",
                "type": el.get("Type") or "",
                "description": desc_text(el),
                "formal": formal_expression_text(el),
            }

    # ItemDef elements: variable-level metadata incl. def:Origin.
    itemdefs = {}
    for el in mdv.iter():
        if local(el.tag) == "ItemDef":
            oid = el.get("OID")
            if not oid:
                continue
            origin_el = child(el, "Origin")
            otype = ""
            odesc = ""
            docrefs = []
            if origin_el is not None:
                otype = origin_el.get("Type") or ""
                odesc = desc_text(origin_el)
                docrefs = origin_docrefs(origin_el)
            itemdefs[oid] = {
                "oid": oid,
                "name": (el.get("Name") or "").upper(),
                "label": desc_text(el),
                "dtype": el.get("DataType") or "",
                "length": el.get("Length") or "",
                "origin_type": otype,
                "origin_desc": odesc,
                "docrefs": docrefs,
            }

    # ItemGroupDef elements: datasets and their ordered ItemRefs.
    # Per the Define-XML 2.0 spec, an ItemRef carries MethodOID to link a
    # variable to its derivation method (def:MethodDef).
    datasets = {}
    variables = {}
    for ig in mdv:
        if local(ig.tag) != "ItemGroupDef":
            continue
        name = (ig.get("Name") or "").upper()
        if not name:
            continue
        cls = (def_attr(ig, "Class") or "").upper()
        if "ADAM" in cls:
            role = "ADAM"
        elif name.startswith("AD"):
            role = "ADAM"          # ADSL, ADAE, ... (fallback heuristic)
        else:
            role = "SDTM"
        leaf_id = def_attr(ig, "ArchiveLocationID")
        href = leaves.get(leaf_id, "") if leaf_id else ""
        xpt = os.path.basename(href) if href else (name.lower() + ".xpt")

        var_order = []
        for ir in children(ig, "ItemRef"):
            item_oid = ir.get("ItemOID")
            idef = itemdefs.get(item_oid)
            if idef is None or not idef["name"]:
                continue
            # MethodOID is a plain attribute on ItemRef in the spec example;
            # also accept a def-namespaced variant for tolerant parsing.
            method_oid = ir.get("MethodOID") or def_attr(ir, "MethodOID") or ""
            vname = idef["name"]
            var_order.append(vname)
            variables["%s.%s" % (name, vname)] = {
                "dataset": name,
                "name": vname,
                "label": idef["label"],
                "dtype": idef["dtype"],
                "length": idef["length"],
                "origin_type": idef["origin_type"],
                "origin_desc": idef["origin_desc"],
                "docrefs": idef["docrefs"],
                "method_oid": method_oid,
            }
        datasets[name] = {
            "name": name,
            "label": desc_text(ig),
            "domain": ig.get("Domain") or name,
            "role": role,
            "xpt": xpt,
            "var_order": var_order,
        }

    return datasets, variables, methods, leaves

# ---------------------------------------------------------------------------
# XPT reading
# ---------------------------------------------------------------------------

def collect_study_files(study_dir):
    """(relative paths, basenames) of every file under study_dir, for
    checking that DocumentRef leaves point at files in the package."""
    rel, base = set(), set()
    for root, _dirs, files in os.walk(study_dir):
        for f in files:
            p = os.path.relpath(os.path.join(root, f), study_dir)
            rel.add(p.replace(os.sep, "/"))
            base.add(f)
    return rel, base


def resolve_leaf_file(href, study_files):
    """Is the file a leaf href points at present in the study folder?
    Returns (found, kind) with kind 'local', 'external', or 'missing'.
    External (http/https) documents cannot be verified locally and are
    treated as present; the paper states that limit."""
    if not href:
        return False, "missing"
    h = href.split("#")[0].split("?")[0].replace("\\", "/").lstrip("./").lstrip("/")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", h):
        return True, "external"
    rel, base = study_files
    if h in rel or os.path.basename(h) in base:
        return True, "local"
    return False, "missing"


def find_xpt_files(study_dir):
    """Map UPPER(file stem) -> path for every *.xpt under study_dir[/xpt]."""
    found = {}
    for base in (study_dir, os.path.join(study_dir, "xpt")):
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            if f.lower().endswith(".xpt"):
                found[os.path.splitext(f)[0].upper()] = os.path.join(base, f)
    return found


def read_xpt(path):
    """Return (upper-cased variable names, row count) for an XPT file."""
    df, meta = pyreadstat.read_xport(path)
    names = [str(c).upper() for c in meta.column_names]
    return names, len(df)


# ---------------------------------------------------------------------------
# Lineage: resolve each variable's predecessor variables
# ---------------------------------------------------------------------------

DOTTED_REF = re.compile(r"\b([A-Z][A-Z0-9]{0,7})\.([A-Z][A-Z0-9_]{0,7})\b")
BARE_TOKEN = re.compile(r"\b([A-Z][A-Z0-9_]{2,8})\b")
# Words that look like variable names but are just derivation prose.
STOPWORDS = {
    "AND", "OR", "NOT", "IF", "THEN", "ELSE", "WHEN", "SET", "THE", "FOR",
    "FROM", "WHERE", "WITH", "NULL", "INTO", "AS", "IS", "TO", "OF", "ON",
    "BY", "AT", "BE", "AN", "DERIVED", "MAPPED", "VALUE", "VALUES",
}


def extract_refs(text, known_vars):
    """Find predecessor variable references in free derivation text.

    Returns (refs, dangling):
      refs     - {"DS.VAR"} tokens that exist in the study metadata
      dangling - {"DS.VAR"} tokens that look explicit but match nothing
    Explicit dotted tokens (DM.AGE) are authoritative; bare tokens only
    count when they uniquely match one known variable name.
    """
    refs, dangling = set(), set()
    if not text:
        return refs, dangling
    t = text.upper()
    for m in DOTTED_REF.finditer(t):
        key = "%s.%s" % (m.group(1), m.group(2))
        if key in known_vars:
            refs.add(key)
        else:
            dangling.add(key)
    by_name = {}
    for k in known_vars:
        by_name.setdefault(k.split(".", 1)[1], []).append(k)
    for m in BARE_TOKEN.finditer(t):
        tok = m.group(1)
        if tok in STOPWORDS:
            continue
        hits = by_name.get(tok, [])
        if len(hits) == 1:
            refs.add(hits[0])
    return refs, dangling


def resolve_predecessors(datasets, variables, methods):
    """For every variable, resolve predecessor variable keys.

    Sources, in order: linked def:MethodDef text (via ItemRef/@MethodOID),
    inline def:Origin description text, and Origin Type='Predecessor'
    (same-named variable in an SDTM dataset).
    Returns {varkey: {'refs': set, 'dangling': set, 'via': str}}.
    """
    known_vars = set(variables)
    sdtm_ds = [n for n, d in datasets.items() if d["role"] == "SDTM"]
    sdtm_names = {}
    for ds in sdtm_ds:
        for v in datasets[ds]["var_order"]:
            sdtm_names.setdefault(v, []).append(ds)

    pred = {}
    for vkey, var in variables.items():
        refs, dangling, via = set(), set(), []
        if var["method_oid"]:
            m = methods.get(var["method_oid"])
            if m:
                r, d = extract_refs(
                    (m["description"] + " " + m["formal"]).strip(), known_vars)
                refs |= r
                dangling |= d
                via.append("MethodDef %s" % m["oid"])
            else:
                via.append("MethodDef %s (missing!)" % var["method_oid"])
        if var["origin_desc"]:
            r, d = extract_refs(var["origin_desc"], known_vars)
            refs |= r
            dangling |= d
            if r or d:
                via.append("Origin description")
        if var["origin_type"] == "Predecessor" and not refs:
            # Fallback when the derivation text names no explicit source:
            # link to the same-named variable in SDTM datasets.
            for ds in sdtm_names.get(var["name"], []):
                refs.add("%s.%s" % (ds, var["name"]))
            if sdtm_names.get(var["name"]):
                via.append("Origin=Predecessor")
        refs.discard(vkey)  # a variable is never its own predecessor
        pred[vkey] = {"refs": refs, "dangling": dangling,
                      "via": "; ".join(via) if via else "none"}
    return pred


def reaches_sdtm(vkey, pred, variables, datasets):
    """True if following predecessor edges reaches any SDTM variable."""
    seen = {vkey}
    queue = deque([vkey])
    while queue:
        cur = queue.popleft()
        for nxt in pred.get(cur, {}).get("refs", ()):
            if nxt in seen:
                continue
            seen.add(nxt)
            ds = variables[nxt]["dataset"]
            if datasets[ds]["role"] == "SDTM":
                return True
            queue.append(nxt)
    return False


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------
# (a) variable in XPT but missing from define.xml
# (b) variable in define.xml but missing from XPT
# (c) ADaM variable with Origin='Derived' but no documented derivation
# (d) derivation references a variable that exists nowhere
# (e) ADaM variable with no lineage path back to any SDTM variable

GAP_LABELS = {
    "XPT_ONLY": "In XPT, missing from define.xml",
    "DEFINE_ONLY": "In define.xml, missing from XPT",
    "DERIVED_NO_METHOD": "Origin=Derived but no derivation documented",
    "DANGLING_REF": "Derivation references undefined variable",
    "NO_SDTM_PATH": "No lineage path back to any SDTM variable",
    # raw-leg classes (SDTM variables only): the source behind SDTM
    "CRF_NO_DOCREF": "Origin=CRF but no annotated CRF link (DocumentRef)",
    "CRF_NO_PAGE": "Annotated CRF link (DocumentRef) names no page",
    "DOCREF_MISSING": "Annotated CRF link points at a document not in the package",
    "EDT_NO_SOURCE": "Origin=eDT but no outside source named",
    "SDTM_DERIVED_NO_METHOD": "SDTM Origin=Derived but no derivation documented",
}

RAW_GAP_CODES = {
    "CRF_NO_DOCREF", "CRF_NO_PAGE", "DOCREF_MISSING",
    "EDT_NO_SOURCE", "SDTM_DERIVED_NO_METHOD",
}


def method_documented(var, methods):
    """A derivation counts as documented with a linked MethodDef that has
    text, or with an inline Origin description."""
    if var["method_oid"]:
        m = methods.get(var["method_oid"])
        if m and (m["description"] or m["formal"]):
            return True
    return bool(var["origin_desc"].strip())


def detect_gaps(datasets, variables, methods, pred, xpt_vars, leaves=None,
                study_files=None):
    gaps = []
    leaves = leaves or {}
    study_files = study_files or (set(), set())

    def add(code, dataset, variable, detail):
        gaps.append({"code": code, "label": GAP_LABELS[code],
                     "dataset": dataset, "variable": variable,
                     "detail": detail})

    # (a) + (b): define.xml vs XPT agreement, per dataset
    for dsname, ds in datasets.items():
        stem = os.path.splitext(ds["xpt"])[0].upper()
        actual = xpt_vars.get(stem)
        defined = set(ds["var_order"])
        if actual is None:
            add("DEFINE_ONLY", dsname, "(dataset)",
                "define.xml dataset has no matching XPT file (%s)" % ds["xpt"])
            continue
        actual_set = set(actual)
        for v in sorted(actual_set - defined):
            add("XPT_ONLY", dsname, v,
                "present in %s but not defined in define.xml" % ds["xpt"])
        for v in sorted(defined - actual_set):
            add("DEFINE_ONLY", dsname, v,
                "defined in define.xml but missing from %s" % ds["xpt"])

    # (c), (d), (e): derivation-level checks
    for vkey, var in variables.items():
        ds = var["dataset"]
        if datasets[ds]["role"] != "ADAM":
            continue
        if var["origin_type"] == "Derived" and not method_documented(var, methods):
            add("DERIVED_NO_METHOD", ds, var["name"],
                "Origin is 'Derived' but no MethodDef/description documents how")
        for d in sorted(pred[vkey]["dangling"]):
            add("DANGLING_REF", ds, var["name"],
                "derivation references %s, which is defined nowhere" % d)
        if not reaches_sdtm(vkey, pred, variables, datasets):
            add("NO_SDTM_PATH", ds, var["name"],
                "no predecessor chain reaches any SDTM variable")

    # (f)-(j): raw-leg checks (SDTM variables only) -- the source behind SDTM
    for vkey, var in variables.items():
        ds = var["dataset"]
        if datasets[ds]["role"] == "ADAM":
            continue
        ot = var["origin_type"]
        if ot == "CRF":
            if not var["docrefs"]:
                add("CRF_NO_DOCREF", ds, var["name"],
                    "Origin is 'CRF' but no DocumentRef links it to an annotated CRF")
            else:
                for dr in var["docrefs"]:
                    if not dr["has_page"]:
                        add("CRF_NO_PAGE", ds, var["name"],
                            "DocumentRef (leaf %s) names no page"
                            % (dr["leaf_id"] or "?"))
                    elif dr["leaf_id"] not in leaves:
                        add("DOCREF_MISSING", ds, var["name"],
                            "DocumentRef leaf %s is not defined in define.xml"
                            % dr["leaf_id"])
                    else:
                        found, _kind = resolve_leaf_file(leaves[dr["leaf_id"]],
                                                         study_files)
                        if not found:
                            add("DOCREF_MISSING", ds, var["name"],
                                "DocumentRef leaf %s points at %s, which is not in the study folder"
                                % (dr["leaf_id"], leaves[dr["leaf_id"]]))
        elif ot == "eDT":
            if not var["origin_desc"].strip():
                add("EDT_NO_SOURCE", ds, var["name"],
                    "Origin is 'eDT' but no outside source is named")
        elif ot == "Derived":
            if not method_documented(var, methods):
                add("SDTM_DERIVED_NO_METHOD", ds, var["name"],
                    "Origin is 'Derived' but no MethodDef/description documents how")

    # stable ordering: by gap type, then dataset, then variable
    order = list(GAP_LABELS)
    gaps.sort(key=lambda g: (order.index(g["code"]), g["dataset"], g["variable"]))
    return gaps

# ---------------------------------------------------------------------------
# HTML report (single self-contained file: inline SVG + JS, no CDN)
# ---------------------------------------------------------------------------

CSS = """
body{font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
 margin:0;background:#f6f8fb;color:#1c2733}
header{background:#0f2a44;color:#fff;padding:22px 32px}
header h1{margin:0;font-size:22px} header p{margin:6px 0 0;color:#b9c8da}
main{padding:24px 32px;max-width:1400px}
.cards{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}
.card{background:#fff;border:1px solid #dfe6ef;border-radius:10px;
 padding:14px 20px;min-width:150px}
.card .n{font-size:26px;font-weight:700}.card .l{font-size:12px;color:#5b6b7f}
.card.bad .n{color:#c0392b}
h2{font-size:17px;margin:26px 0 10px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #dfe6ef;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#eaf0f7}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;
 font-weight:700;white-space:nowrap}
.XPT_ONLY{background:#fdebd0;color:#935116}
.DEFINE_ONLY{background:#f9ebea;color:#922b21}
.DERIVED_NO_METHOD{background:#f5cba7;color:#7e5109}
.DANGLING_REF{background:#fadbd8;color:#943126}
.NO_SDTM_PATH{background:#e8daef;color:#6c3483}
.CRF_NO_DOCREF{background:#d6eaf8;color:#1a5276}
.CRF_NO_PAGE{background:#d5f5e3;color:#1e8449}
.DOCREF_MISSING{background:#f9e79f;color:#7d6608}
.EDT_NO_SOURCE{background:#fad7a0;color:#935116}
.SDTM_DERIVED_NO_METHOD{background:#d2b4de;color:#6c3483}
#graph{background:#fff;border:1px solid #dfe6ef;border-radius:10px;
 overflow:auto;margin-top:6px}
.node{cursor:pointer}
.node rect{fill:#fff;stroke:#7f9bb3;stroke-width:1.5;rx:6}
.node text{font-size:12px;fill:#1c2733}
.node.gapvar rect{stroke:#c0392b;stroke-width:2.5}
.node.xptonly rect{stroke-dasharray:6 4;fill:#fdf6ec}
.node.dim{opacity:.18}.edge.dim{opacity:.08}
.node.hl rect{stroke:#0f2a44;stroke-width:3}
.edge{stroke:#7f9bb3;stroke-width:1.5;fill:none;opacity:.75}
.edge.hl{stroke:#0f2a44;stroke-width:3;opacity:1}
.dslabel{font-size:14px;font-weight:700;fill:#0f2a44}
#detail{background:#fff;border:1px solid #dfe6ef;border-radius:10px;
 padding:14px 18px;margin-top:14px;font-size:13px;min-height:90px}
#detail h3{margin:0 0 8px;font-size:15px}
.kv{color:#5b6b7f}.legend{font-size:12px;color:#5b6b7f;margin-top:8px}
footer{padding:18px 32px;color:#8a97a8;font-size:12px}
"""

JS = """
function clearHl(){
 document.querySelectorAll('.node.hl,.edge.hl,.node.dim,.edge.dim')
  .forEach(function(e){e.classList.remove('hl','dim')});
 document.getElementById('detail').innerHTML='<span class="kv">Click a variable node to inspect its lineage.</span>';
}
function showDetail(id){
 var v=VAR_INFO[id]; if(!v) return;
 var h='<h3>'+v.key+'</h3>';
 h+='<div><span class="kv">Label:</span> '+v.label+'</div>';
 h+='<div><span class="kv">Origin:</span> '+v.origin+'</div>';
 h+='<div><span class="kv">Raw source:</span> '+v.raw+'</div>';
 h+='<div><span class="kv">Method:</span> '+v.method+'</div>';
 h+='<div><span class="kv">Predecessors:</span> '+(v.preds.length?v.preds.join(', '):'<i>none</i>')+'</div>';
 h+='<div><span class="kv">Resolved via:</span> '+v.via+'</div>';
 if(v.gaps.length){h+='<div style="margin-top:6px"><span class="kv">Gaps:</span><ul style="margin:4px 0">'; 
  v.gaps.forEach(function(g){h+='<li><b>'+g.code+'</b> - '+g.detail+'</li>'});h+='</ul></div>'}
 document.getElementById('detail').innerHTML=h;
}
document.querySelectorAll('.node').forEach(function(n){
 n.addEventListener('click',function(ev){
  ev.stopPropagation();
  var id=n.id, wasHl=n.classList.contains('hl');
  clearHl(); if(wasHl) return;
  var keep={}; keep[id]=1;
  document.querySelectorAll('.edge').forEach(function(e){
   if(e.dataset.from===id||e.dataset.to===id){
    e.classList.add('hl');keep[e.dataset.from]=1;keep[e.dataset.to]=1;
   } else e.classList.add('dim');
  });
  document.querySelectorAll('.node').forEach(function(m){
   if(keep[m.id]) m.classList.add('hl'); else m.classList.add('dim');
  });
  showDetail(id);
 });
});
document.getElementById('svgroot').addEventListener('click',clearHl);
"""


def node_id(key):
    return "n-" + key.replace(".", "-")


def raw_source_desc(var, methods, leaves):
    """Plain-English description of a variable's raw source, for the
    report's detail panel."""
    ot = var["origin_type"]
    if ot == "CRF":
        if not var["docrefs"]:
            return "CRF, no annotated CRF link"
        parts = []
        for dr in var["docrefs"]:
            href = leaves.get(dr["leaf_id"], "") if dr["leaf_id"] else ""
            doc = os.path.basename(href) if href else "undefined leaf"
            page = ("p. " + dr["page"]) if dr["has_page"] else "no page"
            parts.append("%s (%s, %s)" % (dr["leaf_id"] or "?", doc, page))
        return "CRF: " + "; ".join(parts)
    if ot == "eDT":
        return "eDT: " + (var["origin_desc"] or "no source named")
    if ot == "Derived":
        return "Derived: " + ("documented" if method_documented(var, methods)
                              else "undocumented")
    return "-"


def coverage_stats(datasets, variables, methods, pred, gaps):
    """((adam_covered, adam_total), (raw_covered, raw_total)).
    ADaM coverage: variables with a predecessor chain reaching SDTM.
    Raw-leg coverage: SDTM variables whose origin needs a raw source
    (CRF, eDT, Derived, in a readable 2.x form) and that carry no raw-leg gap."""
    adam_keys = [k for k, v in variables.items()
                 if datasets[v["dataset"]]["role"] == "ADAM"]
    adam_cov = sum(1 for k in adam_keys
                   if reaches_sdtm(k, pred, variables, datasets))
    raw_keys = [k for k, v in variables.items()
                if datasets[v["dataset"]]["role"] != "ADAM"
                and v["origin_type"] in ("CRF", "eDT", "Derived")]
    raw_gap_vars = {g["dataset"] + "." + g["variable"] for g in gaps
                    if g["code"] in RAW_GAP_CODES}
    raw_cov = sum(1 for k in raw_keys if k not in raw_gap_vars)
    return (adam_cov, len(adam_keys)), (raw_cov, len(raw_keys))


def coverage_text(cov):
    """Two short coverage lines for the console summary and the report."""
    (adam_n, adam_d), (raw_n, raw_d) = cov
    adam = ("%d of %d (%.1f%%)" % (adam_n, adam_d, 100.0 * adam_n / adam_d)
            if adam_d else "n/a")
    raw = ("%d of %d (%.1f%%)" % (raw_n, raw_d, 100.0 * raw_n / raw_d)
           if raw_d else "n/a -- no SDTM variables with a readable origin")
    return adam, raw


def build_html(study_name, define_name, datasets, variables, methods, pred,
               edges, gaps, xpt_vars, xpt_rows, coverage=None, leaves=None):
    esc = html_lib.escape
    n_vars = len(variables)
    n_edges = len(edges)
    n_gaps = len(gaps)
    by_type = {}
    for g in gaps:
        by_type[g["code"]] = by_type.get(g["code"], 0) + 1

    # ---- layout: one column per dataset (SDTM left, ADaM right) ----
    # SDTM datasets that feed ADaM variables sit closest to the ADaM
    # columns, so lineage edges cross empty space instead of other boxes.
    sdtm = sorted([n for n, d in datasets.items() if d["role"] == "SDTM"])
    adam = sorted([n for n, d in datasets.items() if d["role"] != "SDTM"])
    adam_set = set(adam)

    def _feeds_adam(ds):
        for (a, b) in edges:
            da, db = a.split(".", 1)[0], b.split(".", 1)[0]
            if (da == ds and db in adam_set) or (db == ds and da in adam_set):
                return True
        return False

    sdtm = ([d for d in sdtm if not _feeds_adam(d)]
            + [d for d in sdtm if _feeds_adam(d)])
    cols = sdtm + adam
    NW, NH, VGAP, COLW, TOP, LEFT = 230, 30, 12, 300, 100, 40
    pos = {}   # varkey -> (x, y)
    for ci, ds in enumerate(cols):
        x = LEFT + ci * COLW
        # define vars first (in define order), then XPT-only vars (dashed)
        keys = ["%s.%s" % (ds, v) for v in datasets[ds]["var_order"]]
        stem = os.path.splitext(datasets[ds]["xpt"])[0].upper()
        defined = set(datasets[ds]["var_order"])
        for v in (xpt_vars.get(stem) or []):
            if v not in defined:
                keys.append("%s.%s" % (ds, v))
        for i, k in enumerate(keys):
            pos[k] = (x, TOP + i * (NH + VGAP))
    ncols = max(len(cols), 1)
    maxrows = max((len([k for k in pos if k.startswith(c + ".")])
                   for c in cols), default=1)
    W = LEFT * 2 + ncols * COLW
    H = TOP + maxrows * (NH + VGAP) + 50

    # ---- SVG ----
    svg = ['<svg id="svgroot" width="%d" height="%d" '
           'xmlns="http://www.w3.org/2000/svg">' % (W, H)]
    for ci, ds in enumerate(cols):
        x = LEFT + ci * COLW
        svg.append('<text class="dslabel" x="%d" y="40">%s</text>' % (x, esc(ds)))
        svg.append('<text x="%d" y="60" font-size="11" fill="#5b6b7f">%s - %s</text>'
                   % (x, esc(datasets[ds]["role"]),
                      esc(datasets[ds]["xpt"])))
    gap_vars = {g["dataset"] + "." + g["variable"] for g in gaps}
    for k, (x, y) in pos.items():
        nid = node_id(k)
        cls = "node"
        if k in gap_vars:
            cls += " gapvar"
        if k not in variables:
            cls += " xptonly"
        short = k if len(k) <= 26 else k[:25] + "…"
        svg.append(
            '<g class="%s" id="%s"><rect x="%d" y="%d" width="%d" height="%d" rx="6"/>'
            '<text x="%d" y="%d">%s</text></g>'
            % (cls, nid, x, y, NW, NH, x + 10, y + 20, esc(short)))
    for (a, b) in edges:
        if a not in pos or b not in pos:
            continue
        x1, y1 = pos[a][0] + NW, pos[a][1] + NH / 2
        x2, y2 = pos[b][0], pos[b][1] + NH / 2
        mx = (x1 + x2) / 2
        svg.append(
            '<path class="edge" data-from="%s" data-to="%s" '
            'd="M %d %d C %d %d, %d %d, %d %d"/>'
            % (node_id(a), node_id(b), x1, y1, mx, y1, mx, y2, x2, y2))
    svg.append("</svg>")
    svg_html = "\n".join(svg)

    # ---- gap table ----
    rows = []
    for i, g in enumerate(gaps, 1):
        rows.append(
            "<tr><td>%d</td><td><span class='badge %s'>%s</span></td>"
            "<td>%s</td><td><b>%s</b></td><td>%s</td></tr>"
            % (i, g["code"], esc(g["code"]), esc(g["dataset"]),
               esc(g["variable"]), esc(g["detail"])))
    gap_table = ("\n".join(rows) if rows
                 else "<tr><td colspan='5'>No gaps found.</td></tr>")

    # ---- JS detail data ----
    gaps_by_var = {}
    for g in gaps:
        gaps_by_var.setdefault(g["dataset"] + "." + g["variable"], []).append(
            {"code": g["code"], "detail": g["detail"]})
    var_info = {}
    for k, v in variables.items():
        m = methods.get(v["method_oid"]) if v["method_oid"] else None
        var_info[node_id(k)] = {
            "key": k,
            "label": esc(v["label"] or "-"),
            "origin": esc(v["origin_type"] or "-"),
            "method": esc(("%s: %s" % (m["oid"], m["description"] or m["formal"] or "-"))
                          if m else ("(%s, undocumented)" % v["method_oid"]
                                     if v["method_oid"] else "-")),
            "preds": sorted(pred[k]["refs"]),
            "via": esc(pred[k]["via"]),
            "raw": esc(raw_source_desc(v, methods, leaves or {})),
            "gaps": gaps_by_var.get(k, []),
        }
    for k in pos:
        if k not in variables:  # XPT-only nodes
            ds, vn = k.split(".", 1)
            var_info[node_id(k)] = {
                "key": k, "label": "-", "origin": "not in define.xml",
                "method": "-", "preds": [], "via": "none", "raw": "-",
                "gaps": gaps_by_var.get(k, []),
            }

    cards = [
        ("Datasets", len(datasets), ""),
        ("Variables (define.xml)", n_vars, ""),
        ("XPT files read", len(xpt_vars), ""),
        ("Lineage edges", n_edges, ""),
        ("Traceability gaps", n_gaps, "bad" if n_gaps else ""),
    ]
    cards_html = "".join(
        '<div class="card %s"><div class="n">%d</div><div class="l">%s</div></div>'
        % (c, n, l) for l, n, c in cards)

    if coverage is not None:
        adam_txt, raw_txt = coverage_text(coverage)
        cov_html = ("<p id='coverage' style='font-size:13px;color:#5b6b7f;"
                    "margin:4px 0 0'>ADaM&rarr;SDTM lineage coverage: %s"
                    " &nbsp;&middot;&nbsp; Raw&rarr;SDTM links: %s</p>"
                    % (adam_txt, raw_txt))
    else:
        cov_html = ""

    type_rows = "".join(
        "<tr><td><span class='badge %s'>%s</span></td><td>%s</td><td>%d</td></tr>"
        % (code, esc(code), esc(GAP_LABELS[code]), by_type.get(code, 0))
        for code in GAP_LABELS)

    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Traceability report - %s</title><style>%s</style></head><body>"
            "<header><h1>Traceability report: %s</h1>"
            "<p>define.xml: %s &nbsp;|&nbsp; generated by trace.py</p></header><main>"
            "<div class='cards'>%s</div>%s"
            "<h2>Gaps by type</h2><table><tr><th>Type</th><th>Meaning</th><th>Count</th></tr>%s</table>"
            "<h2>Gap details (%d)</h2>"
            "<table><tr><th>#</th><th>Type</th><th>Dataset</th><th>Variable</th><th>Detail</th></tr>%s</table>"
            "<h2>Lineage graph</h2>"
            "<div class='legend'>SDTM datasets on the left, ADaM on the right. "
            "Red border = variable has gaps. Dashed = in XPT but not in define.xml. "
            "Click a node to highlight its lineage.</div>"
            "<div id='graph'>%s</div><div id='detail'>"
            "<span class='kv'>Click a variable node to inspect its lineage.</span></div>"
            "</main><footer>trace.py v1.1.0 - raw-to-ADaM lineage from "
            "define.xml + XPT</footer>"
            "<script>var VAR_INFO = %s;</script><script>%s</script>"
            "</body></html>"
            % (esc(study_name), CSS, esc(study_name), esc(define_name),
               cards_html, cov_html, type_rows, n_gaps, gap_table, svg_html,
               json.dumps(var_info), JS))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_define(study_dir):
    for f in sorted(os.listdir(study_dir)):
        if f.lower() == "define.xml":
            return os.path.join(study_dir, f)
    raise FileNotFoundError("No define.xml found in %s" % study_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build a variable-level lineage graph from define.xml + "
                    "XPT and flag traceability gaps.")
    ap.add_argument("--study", required=True,
                    help="Study folder with define.xml and *.xpt files")
    ap.add_argument("--out", required=True, help="Output HTML report path")
    args = ap.parse_args(argv)

    study = args.study
    define_path = find_define(study)
    datasets, variables, methods, leaves = parse_define(define_path)
    study_files = collect_study_files(study)

    xpt_files = find_xpt_files(study)
    xpt_vars, xpt_rows = {}, {}
    for stem, path in sorted(xpt_files.items()):
        try:
            names, nrows = read_xpt(path)
        except Exception as e:  # noqa: BLE001 - report and continue
            print("warning: could not read %s: %s" % (path, e), file=sys.stderr)
            continue
        xpt_vars[stem] = names
        xpt_rows[stem] = nrows

    pred = resolve_predecessors(datasets, variables, methods)
    gaps = detect_gaps(datasets, variables, methods, pred, xpt_vars,
                       leaves, study_files)
    edges = sorted({(v, r) for v, p in pred.items() for r in p["refs"]})
    cov = coverage_stats(datasets, variables, methods, pred, gaps)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(os.path.basename(os.path.abspath(study)),
                           os.path.basename(define_path),
                           datasets, variables, methods, pred, edges, gaps,
                           xpt_vars, xpt_rows, cov, leaves))

    # ---- console summary ----
    print("Study: %s" % study)
    print("define.xml: %d datasets, %d variables, %d methods"
          % (len(datasets), len(variables), len(methods)))
    for stem in sorted(xpt_vars):
        print("  XPT %-8s %3d vars %4d rows"
              % (stem, len(xpt_vars[stem]), xpt_rows[stem]))
    print("Lineage edges: %d" % len(edges))
    adam_txt, raw_txt = coverage_text(cov)
    print("ADaM lineage coverage: %s" % adam_txt)
    print("Raw-leg coverage: %s" % raw_txt)
    print("Gaps: %d" % len(gaps))
    for g in gaps:
        print("  [%s] %s.%s -- %s"
              % (g["code"], g["dataset"], g["variable"], g["detail"]))
    print("Report written to %s" % args.out)


if __name__ == "__main__":
    main()
