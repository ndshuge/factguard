#!/usr/bin/env python3
"""FactGuard deterministic evidence pipeline. No network calls or model APIs."""
from __future__ import annotations
import argparse
import hashlib
import json
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
HAN = re.compile(r"[\u3400-\u9fff]")
NUM = re.compile(r"(?<![0-9A-Za-z])[-+]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:万亿美元|万美元|亿美元|美元|万亿元|亿元|万元|元|%|％|万亿|亿|万|倍|ms|毫秒|秒/token|秒|TOPS/W|GiB|MiB|GB|MB|PPL)?")
TOPIC = "大模型技术发展及智能体应用研究报告"
CHAPTERS = ["行业概述", "推理优化与模型压缩", "多模态训练与应用", "智能体设计与协作", "市场格局与资本趋势", "成本与产业化分析", "趋势与展望"]

def dump(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def xml_text(element):
    return "".join(n.text or "" for n in element.iter(W + "t")).strip()

def section(locator, text):
    text = text.strip()
    return {"locator": locator, "text": text,
            "numbers": [m.group().strip() for m in NUM.finditer(text)],
            "qualifiers": [k for k in ("预测", "预计", "评估", "模拟", "实测", "观点", "约", "估计", "截至") if k in text]}

def parse_docx(path):
    sections = []
    with zipfile.ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find(W + "body")
        if body is None:
            raise ValueError("DOCX has no body")
        paragraph_index, table_index = 0, 0
        for node in body:
            if node.tag == W + "p":
                paragraph_index += 1
                text = xml_text(node)
                if text:
                    sections.append(section("段落%04d" % paragraph_index, text))
            elif node.tag == W + "tbl":
                table_index += 1
                for ri, row in enumerate(node.findall(W + "tr"), 1):
                    for ci, cell in enumerate(row.findall(W + "tc"), 1):
                        text = " ".join(xml_text(p) for p in cell.findall(".//" + W + "p")).strip()
                        if text:
                            sections.append(section("表%d/行%d/列%d" % (table_index, ri, ci), text))
    return sections

def parse_pdf(path):
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError as e:
            raise RuntimeError("PDF parser unavailable: install PyMuPDF in authorized environment") from e
    result = []
    with pymupdf.open(str(path)) as doc:
        for i, page in enumerate(doc, 1):
            text = page.get_text("text", sort=True).strip()
            if not text:
                raise ValueError("PDF page %d has no extractable text (scanned page; OCR required)" % i)
            for block, textpart in enumerate(re.split(r"\n\s*\n", text), 1):
                textpart = " ".join(textpart.split())
                if textpart:
                    result.append(section("PDF第%d页/块%d" % (i, block), textpart))
    return result

def spreadsheet_cell_text(cell, shared):
    typ = cell.get("t")
    if typ == "inlineStr":
        return "".join(x.text or "" for x in cell.findall(".//" + S + "t"))
    value = cell.find(S + "v")
    if value is None or value.text is None:
        if cell.find(S + "f") is not None:
            raise ValueError("XLSX formula without cached value at " + str(cell.get("r")))
        return ""
    if typ == "s":
        try:
            return shared[int(value.text)]
        except (ValueError, IndexError):
            raise ValueError("Invalid shared-string reference: " + value.text)
    return value.text

def parse_xlsx(path):
    sections = []
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for item in shared_root.findall(S + "si"):
                shared.append("".join(t.text or "" for t in item.iter(S + "t")))
        if "xl/workbook.xml" not in z.namelist() or "xl/_rels/workbook.xml.rels" not in z.namelist():
            raise ValueError("XLSX workbook relationships missing; cannot authenticate worksheet names")
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rel_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rel_root.findall(PKG + "Relationship")}
        sheets = []
        for entry in wb.findall(".//" + S + "sheet"):
            title = entry.get("name", "")
            ref = entry.get(R + "id")
            target = targets.get(ref) if ref else None
            if not title or not target:
                raise ValueError("XLSX sheet has no reliable name-to-file mapping")
            if target.startswith("/"):
                name = target.lstrip("/")
            elif target.startswith("xl/"):
                name = posixpath.normpath(target)
            else:
                name = posixpath.normpath("xl/" + target)
            if not name.startswith("xl/worksheets/") or name not in z.namelist():
                raise ValueError("XLSX referenced sheet file invalid or missing")
            sheets.append((title, name))
        if not sheets:
            raise ValueError("No XLSX worksheets found")
        for sheet_name, name in sheets:
            tree = ET.fromstring(z.read(name))
            for row in tree.findall(".//" + S + "sheetData/" + S + "row"):
                for cell in row.findall(S + "c"):
                    text = spreadsheet_cell_text(cell, shared).strip()
                    if text:
                        address = cell.get("r")
                        if not address:
                            raise ValueError("XLSX cell without address")
                        sections.append(section("工作表" + sheet_name + "!" + address, text))
    return sections

def inspect(input_dir, out):
    input_path = Path(input_dir)
    if not input_path.is_dir():
        raise ValueError("input-dir is not a directory")
    sources, errors = [], []
    files = sorted(p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in (".pdf", ".docx", ".xlsx", ".txt", ".md"))
    if not files:
        raise ValueError("No supported files under input-dir")
    for path in files:
        rel = str(path.relative_to(input_path)).replace("\\", "/")
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        obj = {"id": hashlib.sha256((sha+"\0"+rel).encode()).hexdigest()[:16], "filename": rel, "sha256": sha, "type": path.suffix.lower(), "sections": []}
        try:
            ext = path.suffix.lower()
            if ext == ".pdf":
                obj["sections"] = parse_pdf(path)
            elif ext == ".docx":
                obj["sections"] = parse_docx(path)
            elif ext == ".xlsx":
                obj["sections"] = parse_xlsx(path)
            else:
                obj["sections"] = [section("行%d" % i, t) for i,t in enumerate(path.read_text(encoding="utf-8-sig").splitlines(),1) if t.strip()]
            if not obj["sections"]:
                raise ValueError("No extracted content")
            sources.append(obj)
        except Exception as err:
            errors.append({"filename": rel, "type": type(err).__name__, "message": str(err)})
    data = {"schema": "factguard.sources.v1", "sources": sources, "errors": errors, "summary": {
        "file_count": len(files), "parsed": len(sources), "failed": len(errors),
        "sections": sum(len(s["sections"]) for s in sources)}}
    dump(data, out)
    print(json.dumps(data["summary"], ensure_ascii=False))
    return 2 if errors else 0

def index_sources(data):
    return {(s["id"], sec["locator"]): (s, sec) for s in data["sources"] for sec in s["sections"]}

def tokens(text):
    # Numerical comparisons are intentionally conservative: compare values AND written units.
    return [re.sub(r"\s+", "", m.group()) for m in NUM.finditer(text)]

def check_claim(claim, source_index):
    cid = str(claim.get("id", ""))
    kind = claim.get("kind")
    text = claim.get("text", "")
    if kind == "analysis":
        return {"id": cid, "status": "analysis_only", "citations": [], "issues": []}
    issues, citations, quotes = [], [], []
    if kind != "fact" or not text.strip():
        issues.append("invalid_fact_record")
    evidence = claim.get("evidence") or []
    if not evidence:
        issues.append("missing_evidence")
    for item in evidence:
        key = (item.get("source_id"), item.get("locator"))
        record = source_index.get(key)
        if not record:
            issues.append("locator_not_found")
            continue
        source, sec = record
        quote = item.get("quote") or ""
        if not quote or quote not in sec["text"]:
            issues.append("quote_not_found_at_locator")
            continue
        quotes.append(quote)
        start = sec["text"].find(quote)
        citations.append({"source_id": source["id"], "filename": source["filename"], "locator": sec["locator"], "quote": quote,
                          "span": [start, start + len(quote)], "source_numbers": sec.get("numbers", []),
                          "qualifiers": sec.get("qualifiers", [])})
    if citations and not any(text.strip() == q.strip() for q in quotes):
        issues.append("semantic_paraphrase_requires_independent_review")
    if citations:
        tvals = set(tokens(text))
        qvals = set(v for q in quotes for v in tokens(q))
        absent = sorted(tvals - qvals)
        if absent:
            issues.append("number_or_unit_not_in_quote:" + "|".join(absent))
    status = "needs_revision" if issues else "supported_by_material"
    return {"id": cid, "text": text, "status": status, "citations": citations, "issues": sorted(set(issues)),
            "truth_level": "source_quote_only", "material_conflict_review": "manual_or_agent_required"}

def audit_data(sources, claims):
    if sources.get("errors"):
        raise ValueError("source index is incomplete; refuses to audit as complete")
    source_index = index_sources(sources)
    seen, records = set(), []
    for c in claims.get("claims", []):
        if c.get("id") in seen:
            raise ValueError("duplicate claim_id")
        seen.add(c.get("id"))
        records.append(check_claim(c, source_index))
    summary = {"claims": len(records), "supported_by_material": sum(r["status"] == "supported_by_material" for r in records),
               "needs_revision": sum(r["status"] == "needs_revision" for r in records),
               "analysis_only": sum(r["status"] == "analysis_only" for r in records),
               "external_truth_verified": False, "teleagent_execution_verified": False}
    return {"schema": "factguard.audit.v1", "claims": records, "summary": summary}

def normalized_measure(value, unit):
    """Only compare explicitly compatible physical/financial dimensions."""
    from decimal import Decimal, InvalidOperation
    factors = {
        "元": ("CNY", "1"), "万元": ("CNY", "10000"), "亿元": ("CNY", "100000000"),
        "万亿元": ("CNY", "1000000000000"), "万亿": ("CNY", "1000000000000"),
        "美元": ("USD", "1"), "万美元": ("USD", "10000"),
        "亿美元": ("USD", "100000000"), "万亿美元": ("USD", "1000000000000"),
        "ms": ("latency_ms", "1"), "毫秒": ("latency_ms", "1"), "秒": ("latency_ms", "1000"),
        "GB": ("memory_bytes", "1000000000"), "MB": ("memory_bytes", "1000000"),
        "GiB": ("memory_bytes", "1073741824"), "MiB": ("memory_bytes", "1048576"),
        "%": ("percent", "1"), "％": ("percent", "1"), "倍": ("ratio", "1")
    }
    if unit not in factors:
        return None
    dim, factor = factors[unit]
    try:
        return dim, Decimal(str(value).replace(",", "")) * Decimal(factor)
    except InvalidOperation:
        return None

def compare_data(sources, comparisons):
    """Conservatively evaluate reviewer-proposed same-scope groups, never infer identity."""
    if sources.get("errors"):
        raise ValueError("source index incomplete")
    index = index_sources(sources)
    reports = []
    for group in comparisons.get("groups", []):
        gid = group.get("id")
        if not gid or not group.get("scope"):
            raise ValueError("each comparison requires id and explicit scope")
        facts, issues = [], []
        for entry in group.get("evidence", []):
            match = index.get((entry.get("source_id"), entry.get("locator")))
            if not match:
                issues.append("source_locator_missing")
                continue
            source, sec = match
            quote = entry.get("quote", "")
            if not quote or quote not in sec["text"]:
                issues.append("quote_missing_at_locator")
                continue
            number, unit = entry.get("number"), entry.get("unit")
            if number is None or not unit:
                issues.append("number_or_unit_missing")
                continue
            # Do not trust an assigned value unless the exact literal appears within this quote.
            literal = str(number)
            if not re.search(r"(?<![0-9.])" + re.escape(literal) + r"\s*" + re.escape(unit) + r"(?![0-9A-Za-z])", quote):
                issues.append("numeric_unit_pair_not_in_quote")
                continue
            norm = normalized_measure(number, unit)
            if norm is None:
                issues.append("uncomparable_unit")
                continue
            if unit not in quote:
                issues.append("unit_not_in_quote")
                continue
            record = {"source_id": source["id"], "filename": source["filename"],
                      "locator": sec["locator"], "number": str(number),
                      "unit": unit, "dimension": norm[0], "normalized": str(norm[1]),
                      "data_nature": entry.get("data_nature", "unknown")}
            facts.append(record)
        dimensions = set(x["dimension"] for x in facts)
        natures = set(x["data_nature"] for x in facts)
        if len(dimensions) > 1:
            issues.append("dimension_mismatch")
        if len(natures) > 1 or "unknown" in natures:
            issues.append("forecast_observed_opinion_scope_unconfirmed")
        vals = set(Decimal(x["normalized"]) for x in facts)
        if len(facts) < 2:
            verdict = "insufficient_comparable_evidence"
        elif issues:
            verdict = "review_required"
        elif len(vals) > 1:
            verdict = "numeric_disagreement_within_declared_scope"
        else:
            verdict = "numerically_consistent_within_declared_scope"
        reports.append({"id": gid, "scope": group["scope"], "verdict": verdict,
                        "evidence": facts, "issues": sorted(set(issues)),
                        "real_world_truth": "not_verified"})
    return {"schema": "factguard.comparisons.v1", "groups": reports, "teleagent_execution_verified": False}

def docx_package(paragraphs, output):
    # Minimal, self-contained, standards-compliant DOCX without package installation.
    def run(text, bold=False):
        return "<w:r><w:rPr>%s</w:rPr><w:t xml:space=\"preserve\">%s</w:t></w:r>" % ("<w:b/>" if bold else "", escape(text))
    body = []
    for text, style in paragraphs:
        prop = "<w:pPr><w:pStyle w:val=\"%s\"/></w:pPr>" % style if style else ""
        body.append("<w:p>" + prop + run(text) + "</w:p>")
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + "".join(body) + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/><w:color w:val="000000"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="140" w:line="320" w:lineRule="auto"/><w:widowControl/></w:pPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:after="260"/></w:pPr><w:rPr><w:b/><w:color w:val="000000"/><w:sz w:val="34"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/><w:keepNext/><w:spacing w:before="260" w:after="180"/></w:pPr><w:rPr><w:b/><w:color w:val="000000"/><w:sz w:val="28"/></w:rPr></w:style>'
              '</w:styles>')
    ctype = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    wrels = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             '</Relationships>')
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n, t in {"[Content_Types].xml": ctype, "_rels/.rels": rels,
                     "word/document.xml": document, "word/styles.xml": styles,
                     "word/_rels/document.xml.rels": wrels}.items():
            z.writestr(n, t)

def sample(sources, output, audit_out):
    if sources.get("errors") or not sources.get("sources"):
        raise ValueError("source pack incomplete or empty")
    claims = []
    for s in sources["sources"]:
        for sec in s["sections"]:
            if len(sec["text"]) >= 10:
                quote = sec["text"][:min(140, len(sec["text"]))]
                claims.append({"id": "FG0-%02d" % (len(claims)+1), "kind": "fact",
                   "text": quote, "evidence": [{"source_id": s["id"], "locator": sec["locator"], "quote": quote}]})
                break
        if len(claims) >= 3:
            break
    audit = audit_data(sources, {"claims": claims})
    audit["mode"] = "offline_fg0_preview_not_teleagent"
    dump(audit, audit_out)
    paragraphs = [(TOPIC + "（FG0离线短样，仅做Word能力验证）", "Title"),
                  ("【非正式综述，不满足七章/万字要求；不代表TeleAgent加载或真实世界核实】", None)]
    for c, record in zip(claims, audit["claims"]):
        if record["status"] != "supported_by_material":
            raise ValueError("FG0 claim failed audit")
        src = record["citations"][0]
        paragraphs.append(("材料记载：" + c["text"] + "【" + src["filename"] + "，" + src["locator"] + "】", None))
    paragraphs.append(("本样张仅验证可审计引用和Word导出。", None))
    docx_package(paragraphs, output)
    return audit["summary"]

def render(chapters, audit, output):
    items = chapters.get("chapters", [])
    if len(items) != 7:
        raise ValueError("requires exactly 7 chapters")
    if [c.get("title") for c in items] != CHAPTERS:
        raise ValueError("chapter title/order does not match contest theme")
    checked = {c["id"]: c for c in audit.get("claims", [])}
    paragraphs = [(TOPIC, "Title")]
    chinese = 0
    fact_count = 0
    chapter_fact_counts = []
    paragraph_signatures = set()
    for chapter in items:
        chapter_facts = 0
        paragraphs.append((chapter["title"], "Heading1"))
        if not chapter.get("paragraphs"):
            raise ValueError("empty chapter: " + chapter["title"])
        for para in chapter["paragraphs"]:
            content = str(para.get("text", "")).strip()
            if not content:
                raise ValueError("blank chapter paragraph")
            if content in paragraph_signatures:
                raise ValueError("duplicate paragraph would inflate report")
            paragraph_signatures.add(content)
            chinese += len(HAN.findall(content))
            if para.get("kind") == "fact":
                cid = para.get("claim_id")
                record = checked.get(cid)
                if record is None or record["status"] != "supported_by_material" or not record["citations"] or record.get("text") != content:
                    raise ValueError("unreviewed fact claim: " + str(cid))
                source_ref = "；".join("【%s，%s】" % (c["filename"], c["locator"]) for c in record["citations"])
                paragraphs.append((content + source_ref, None))
                fact_count += 1
                chapter_facts += 1
            elif para.get("kind") == "analysis":
                paragraphs.append(("[分析推断] " + content, None))
            else:
                raise ValueError("unknown paragraph kind")
        chapter_fact_counts.append(chapter_facts)
    if chinese < 10000:
        raise ValueError("seven-chapter draft incomplete: %d Han characters < 10000" % chinese)
    if not all(chapter_fact_counts):
        raise ValueError("every chapter must include at least one independently audited source-grounded factual statement")
    docx_package(paragraphs, output)
    return {"han_characters": chinese, "factual_paragraphs": fact_count, "chapters": 7}

def main():
    p = argparse.ArgumentParser(description="FactGuard deterministic source and audit CLI (no model API)")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("inspect"); a.add_argument("--input-dir", required=True); a.add_argument("--out", required=True)
    a = sub.add_parser("sample"); a.add_argument("--sources", required=True); a.add_argument("--out", required=True); a.add_argument("--audit-out", required=True)
    a = sub.add_parser("audit"); a.add_argument("--sources", required=True); a.add_argument("--claims", required=True); a.add_argument("--out", required=True)
    a = sub.add_parser("compare"); a.add_argument("--sources", required=True); a.add_argument("--groups", required=True); a.add_argument("--out", required=True)
    a = sub.add_parser("render"); a.add_argument("--chapters", required=True); a.add_argument("--audit", required=True); a.add_argument("--out", required=True)
    args = p.parse_args()
    try:
        if args.command == "inspect":
            return inspect(args.input_dir, args.out)
        if args.command == "sample":
            result = sample(read_json(args.sources), args.out, args.audit_out)
        elif args.command == "audit":
            result = audit_data(read_json(args.sources), read_json(args.claims))
            dump(result, args.out)
            result = result["summary"]
        elif args.command == "compare":
            result = compare_data(read_json(args.sources), read_json(args.groups))
            dump(result, args.out)
            result = {"groups": len(result["groups"]), "verdicts": [g["verdict"] for g in result["groups"]]}
        else:
            result = render(read_json(args.chapters), read_json(args.audit), args.out)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, KeyError, FileNotFoundError, zipfile.BadZipFile, ET.ParseError) as err:
        print(json.dumps({"error": type(err).__name__, "message": str(err)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
