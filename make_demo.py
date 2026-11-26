#!/usr/bin/env python3
"""
make_demo.py -- Generate a small synthetic study to demo trace.py.

Creates demo_study/ with:
  define.xml   CDISC Define-XML 2.0 for DM, AE, LB (SDTM) and ADSL (ADaM)
  dm.xpt       SDTM demographics
  ae.xpt       SDTM adverse events
  lb.xpt       SDTM laboratory results (two variables only)
  adsl.xpt     ADaM subject level
  acrf.pdf     stub annotated CRF, so clean CRF links resolve

Ten traceability gaps are deliberately planted (see PLANTED_GAPS):
  (a) DM.SITEID        in dm.xpt but missing from define.xml
  (b) DM.RACE          in define.xml but missing from dm.xpt
  (c) ADSL.BMIGR1      Origin='Derived' with no MethodDef/description
  (d) ADSL.SAFFL       derivation references DM.FAKEVAR (undefined)
  (e) ADSL.SAFFL       no lineage path back to any SDTM variable
       (ADSL.BMIGR1 also has no SDTM path, as a consequence of (c))
  (f) AE.AETERM        Origin='CRF' with no DocumentRef (raw leg)
  (g) AE.USUBJID       DocumentRef names no page (raw leg)
  (h) DM.ETHNIC        DocumentRef leaf LF.ACRF2 is not defined (raw leg)
  (i) LB.LBORRESU      Origin='eDT' with no outside source named (raw leg)
  (j) AE.AEDECOD       SDTM Origin='Derived' with no MethodDef (raw leg)

Usage:
    python make_demo.py
"""

import os

import pandas as pd
import pyreadstat

STUDY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "demo_study")

PLANTED_GAPS = """Planted gaps in demo_study (expect trace.py to flag all of these):
(a) XPT_ONLY         DM.SITEID      in dm.xpt, missing from define.xml
(b) DEFINE_ONLY      DM.RACE        in define.xml, missing from dm.xpt
(c) DERIVED_NO_METHOD ADSL.BMIGR1   Origin=Derived, no MethodDef/description
(d) DANGLING_REF     ADSL.SAFFL     derivation references DM.FAKEVAR (undefined)
(e) NO_SDTM_PATH     ADSL.SAFFL     no predecessor chain reaches SDTM
    NO_SDTM_PATH     ADSL.BMIGR1    (consequence of (c): nothing to trace)
(f) CRF_NO_DOCREF    AE.AETERM      Origin=CRF, no DocumentRef to an annotated CRF
(g) CRF_NO_PAGE      AE.USUBJID     DocumentRef names no page
(h) DOCREF_MISSING   DM.ETHNIC      DocumentRef leaf LF.ACRF2 is not defined
(i) EDT_NO_SOURCE    LB.LBORRESU    Origin=eDT, no outside source named
(j) SDTM_DERIVED_NO_METHOD AE.AEDECOD SDTM Origin=Derived, no MethodDef/description
"""


def itemdef(oid, name, dtype, length, label, origin_type, origin_desc="",
            docrefs=None):
    """docrefs: [(leaf_id, page_refs_or_None)] -> def:DocumentRef children."""
    parts = []
    for leaf_id, pages in (docrefs or []):
        if pages:
            parts.append('<def:DocumentRef leafID="%s" PageRefs="%s"/>'
                         % (leaf_id, pages))
        else:
            parts.append('<def:DocumentRef leafID="%s"/>' % leaf_id)
    if origin_desc:
        parts.append('<def:Description><TranslatedText xml:lang="en">%s'
                     '</TranslatedText></def:Description>' % origin_desc)
    if parts:
        origin = ('<def:Origin Type="%s">%s</def:Origin>'
                  % (origin_type, "".join(parts)))
    else:
        origin = '<def:Origin Type="%s"/>' % origin_type
    return (
        '    <ItemDef OID="%s" Name="%s" DataType="%s" Length="%s" SASFieldName="%s">\n'
        '      <Description><TranslatedText xml:lang="en">%s</TranslatedText></Description>\n'
        '      %s\n'
        '    </ItemDef>' % (oid, name, dtype, length, name, label, origin))


DEFINE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Synthetic demo define.xml (CDISC Define-XML 2.0 structure) for trace.py.
     Generated 2026-09-09. Not a real study. -->
<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3"
     xmlns:def="http://www.cdisc.org/ns/def/v2.0"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     ODMVersion="1.3.2" FileOID="TRACE.DEMO.001" FileType="Snapshot"
     CreationDateTime="2026-09-09T00:00:00">
  <Study OID="TRACE-DEMO">
    <GlobalVariables>
      <StudyName>Traceability demo study</StudyName>
      <StudyDescription>Synthetic study for the trace.py prototype</StudyDescription>
      <ProtocolName>TRACE-001</ProtocolName>
    </GlobalVariables>
    <MetaDataVersion OID="MDV.TRACE-DEMO" Name="Demo metadata" DefineVersion="2.0.0">
      <def:leaf ID="LF.DM" xlink:href="dm.xpt">
        <def:title>dm.xpt</def:title>
      </def:leaf>
      <def:leaf ID="LF.AE" xlink:href="ae.xpt">
        <def:title>ae.xpt</def:title>
      </def:leaf>
      <def:leaf ID="LF.ADSL" xlink:href="adsl.xpt">
        <def:title>adsl.xpt</def:title>
      </def:leaf>
      <def:leaf ID="LF.ACRF" xlink:href="acrf.pdf">
        <def:title>acrf.pdf</def:title>
      </def:leaf>
      <def:leaf ID="LF.LB" xlink:href="lb.xpt">
        <def:title>lb.xpt</def:title>
      </def:leaf>

      <!-- ===================== SDTM: DM ===================== -->
      <ItemGroupDef OID="IG.DM" Name="DM" Domain="DM" SASDatasetName="DM"
          Repeating="No" IsReferenceData="No" Purpose="Tabulation"
          def:Class="SPECIAL PURPOSE"
          def:Structure="One record per subject"
          def:ArchiveLocationID="LF.DM">
        <Description><TranslatedText xml:lang="en">Demographics</TranslatedText></Description>
        <ItemRef ItemOID="IT.DM.USUBJID" OrderNumber="1" Mandatory="Yes" KeySequence="1"/>
        <ItemRef ItemOID="IT.DM.AGE" OrderNumber="2" Mandatory="No"/>
        <ItemRef ItemOID="IT.DM.SEX" OrderNumber="3" Mandatory="No"/>
        <ItemRef ItemOID="IT.DM.RACE" OrderNumber="4" Mandatory="No"/>
        <ItemRef ItemOID="IT.DM.ETHNIC" OrderNumber="5" Mandatory="No"/>
      </ItemGroupDef>

      <!-- ===================== SDTM: AE ===================== -->
      <ItemGroupDef OID="IG.AE" Name="AE" Domain="AE" SASDatasetName="AE"
          Repeating="Yes" IsReferenceData="No" Purpose="Tabulation"
          def:Class="EVENTS"
          def:Structure="One record per adverse event per subject"
          def:ArchiveLocationID="LF.AE">
        <Description><TranslatedText xml:lang="en">Adverse Events</TranslatedText></Description>
        <ItemRef ItemOID="IT.AE.USUBJID" OrderNumber="1" Mandatory="Yes" KeySequence="1"/>
        <ItemRef ItemOID="IT.AE.AETERM" OrderNumber="2" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.AE.AEDECOD" OrderNumber="3" Mandatory="No"/>
      </ItemGroupDef>

      <!-- ===================== SDTM: LB ===================== -->
      <ItemGroupDef OID="IG.LB" Name="LB" Domain="LB" SASDatasetName="LB"
          Repeating="Yes" IsReferenceData="No" Purpose="Tabulation"
          def:Class="FINDINGS"
          def:Structure="One record per lab result per subject"
          def:ArchiveLocationID="LF.LB">
        <Description><TranslatedText xml:lang="en">Laboratory Test Results</TranslatedText></Description>
        <ItemRef ItemOID="IT.LB.LBORRES" OrderNumber="1" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.LBORRESU" OrderNumber="2" Mandatory="No"/>
      </ItemGroupDef>

      <!-- ===================== ADaM: ADSL ===================== -->
      <ItemGroupDef OID="IG.ADSL" Name="ADSL" SASDatasetName="ADSL"
          Repeating="No" IsReferenceData="No" Purpose="Analysis"
          def:Class="ADAM"
          def:Structure="One record per subject"
          def:ArchiveLocationID="LF.ADSL">
        <Description><TranslatedText xml:lang="en">Subject-Level Analysis Dataset</TranslatedText></Description>
        <ItemRef ItemOID="IT.ADSL.USUBJID" OrderNumber="1" Mandatory="Yes" KeySequence="1"/>
        <ItemRef ItemOID="IT.ADSL.AGE" OrderNumber="2" Mandatory="No" MethodOID="MT.ADSL.AGE"/>
        <ItemRef ItemOID="IT.ADSL.SEX" OrderNumber="3" Mandatory="No" MethodOID="MT.ADSL.SEX"/>
        <ItemRef ItemOID="IT.ADSL.BMIGR1" OrderNumber="4" Mandatory="No"/>
        <ItemRef ItemOID="IT.ADSL.SAFFL" OrderNumber="5" Mandatory="No" MethodOID="MT.ADSL.SAFFL"/>
      </ItemGroupDef>

      <!-- ===================== ItemDefs ===================== -->
%(itemdefs)s

      <!-- ===================== MethodDefs ===================== -->
      <def:MethodDef OID="MT.ADSL.AGE" Name="Age derivation" Type="Computation">
        <Description><TranslatedText xml:lang="en">Derived directly from DM.AGE.</TranslatedText></Description>
      </def:MethodDef>
      <def:MethodDef OID="MT.ADSL.SEX" Name="Sex mapping" Type="Computation">
        <Description><TranslatedText xml:lang="en">Mapped from DM.SEX.</TranslatedText></Description>
      </def:MethodDef>
      <def:MethodDef OID="MT.ADSL.SAFFL" Name="Safety flag derivation" Type="Computation">
        <Description><TranslatedText xml:lang="en">Set to 'Y' when DM.FAKEVAR equals 'X'.</TranslatedText></Description>
      </def:MethodDef>
    </MetaDataVersion>
  </Study>
</ODM>
"""

ITEMDEFS = [
    # SDTM DM -- clean CRF variables carry a DocumentRef to the aCRF stub
    ("IT.DM.USUBJID", "USUBJID", "text", "20", "Unique Subject Identifier", "CRF",
     "", [("LF.ACRF", "2")]),
    ("IT.DM.AGE", "AGE", "integer", "8", "Age", "CRF",
     "", [("LF.ACRF", "2")]),
    ("IT.DM.SEX", "SEX", "text", "1", "Sex", "CRF",
     "", [("LF.ACRF", "2")]),
    # Planted gap (b): defined here, but the column is absent from dm.xpt.
    # Its CRF link is intact, so (b) is the only gap on this variable.
    ("IT.DM.RACE", "RACE", "text", "20", "Race", "CRF",
     "", [("LF.ACRF", "3")]),
    # Planted gap (h): DocumentRef leaf LF.ACRF2 is not defined anywhere
    ("IT.DM.ETHNIC", "ETHNIC", "text", "20", "Ethnicity", "CRF",
     "", [("LF.ACRF2", "3")]),
    # SDTM AE
    # Planted gap (g): DocumentRef with no page reference
    ("IT.AE.USUBJID", "USUBJID", "text", "20", "Unique Subject Identifier", "CRF",
     "", [("LF.ACRF", None)]),
    # Planted gap (f): CRF origin with no DocumentRef at all
    ("IT.AE.AETERM", "AETERM", "text", "40", "Reported Term for the Adverse Event", "CRF"),
    # Planted gap (j): SDTM Derived with no MethodDef and no description
    ("IT.AE.AEDECOD", "AEDECOD", "text", "40", "Dictionary-Derived Term", "Derived"),
    # SDTM LB (two variables only; no USUBJID in this minimal demo)
    ("IT.LB.LBORRES", "LBORRES", "text", "20", "Result or Finding in Original Units", "eDT",
     "Central laboratory transfer."),
    # Planted gap (i): eDT origin with no outside source named
    ("IT.LB.LBORRESU", "LBORRESU", "text", "20", "Original Units", "eDT"),
    # ADaM ADSL
    ("IT.ADSL.USUBJID", "USUBJID", "text", "20", "Unique Subject Identifier",
     "Predecessor", "Copied from DM.USUBJID."),
    ("IT.ADSL.AGE", "AGE", "integer", "8", "Age", "Derived"),
    ("IT.ADSL.SEX", "SEX", "text", "1", "Sex", "Derived"),
    # Planted gap (c): Derived with no MethodDef and no description
    ("IT.ADSL.BMIGR1", "BMIGR1", "text", "10", "BMI Group 1", "Derived"),
    ("IT.ADSL.SAFFL", "SAFFL", "text", "1", "Safety Population Flag", "Derived"),
]


def build_define_xml():
    parts = [itemdef(*spec) for spec in ITEMDEFS]
    return DEFINE_XML % {"itemdefs": "\n".join(parts)}


def write_acrf_pdf(path):
    """Minimal one-page PDF standing in for the annotated CRF, so that
    clean CRF DocumentRefs resolve to a real file in the study folder."""
    text = "Annotated CRF (demo stub) - TRACE-001"
    content = "BT /F1 14 Tf 72 720 Td (%s) Tj ET" % text.replace(
        "\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ("<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content)
         ).encode("latin-1"),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += ("%d 0 obj\n" % i).encode("latin-1") + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += ("xref\n0 %d\n" % (len(objects) + 1)).encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode("latin-1")
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref_pos)).encode("latin-1")
    with open(path, "wb") as f:
        f.write(bytes(out))


def main():
    os.makedirs(STUDY_DIR, exist_ok=True)

    with open(os.path.join(STUDY_DIR, "define.xml"), "w", encoding="utf-8") as f:
        f.write(build_define_xml())

    # NOTE: dm.xpt deliberately contains SITEID (gap a: not in define.xml)
    # and omits RACE (gap b: defined in define.xml).
    dm = pd.DataFrame({
        "USUBJID": ["D-001", "D-002"],
        "AGE": [45, 52],
        "SEX": ["M", "F"],
        "SITEID": ["S01", "S02"],
        "ETHNIC": ["HISPANIC OR LATINO", "NOT HISPANIC OR LATINO"],
    })
    ae = pd.DataFrame({
        "USUBJID": ["D-001"],
        "AETERM": ["HEADACHE"],
        "AEDECOD": ["HEADACHE"],
    })
    lb = pd.DataFrame({
        "LBORRES": ["5.1", "4.8"],
        "LBORRESU": ["10*9/L", "10*9/L"],
    })
    adsl = pd.DataFrame({
        "USUBJID": ["D-001", "D-002"],
        "AGE": [45, 52],
        "SEX": ["M", "F"],
        "BMIGR1": ["1", "2"],
        "SAFFL": ["Y", "Y"],
    })
    pyreadstat.write_xport(dm, os.path.join(STUDY_DIR, "dm.xpt"),
                           table_name="DM", file_label="Demographics")
    pyreadstat.write_xport(ae, os.path.join(STUDY_DIR, "ae.xpt"),
                           table_name="AE", file_label="Adverse Events")
    pyreadstat.write_xport(adsl, os.path.join(STUDY_DIR, "adsl.xpt"),
                           table_name="ADSL",
                           file_label="Subject-Level Analysis Dataset")
    pyreadstat.write_xport(lb, os.path.join(STUDY_DIR, "lb.xpt"),
                           table_name="LB",
                           file_label="Laboratory Test Results")
    write_acrf_pdf(os.path.join(STUDY_DIR, "acrf.pdf"))

    with open(os.path.join(STUDY_DIR, "planted_gaps.txt"), "w") as f:
        f.write(PLANTED_GAPS)

    print("Demo study written to %s" % STUDY_DIR)
    print(PLANTED_GAPS)


if __name__ == "__main__":
    main()
