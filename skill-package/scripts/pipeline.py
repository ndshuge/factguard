#!/usr/bin/env python3
"""Evidence-bound stages. Standard library only; imports the original parsers.

No model calls, no sample-specific answers. A hosting agent supplies the prose
draft. Independent review runs in another Python process and reopens originals.
"""
from __future__ import annotations
import argparse
import copy
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import factguard as fg
import calculations

VERSION = "factguard.rc1"
MEASURE = re.compile(r"(?<![\dA-Za-z.])([-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(万亿美元|亿美元|万美元|美元|万亿元|亿元|万元|元|毫秒|秒/token|秒|ms|TOPS/W|GB|MB|％|%|倍|万亿|亿|万|token|tok/s|nm|x)(?![A-Za-z])", re.I)
YEAR = re.compile(r"(?:19|20)\d{2}(?:年(?:\d{1,2}月)?)?")
INJECTION = re.compile(r"(?:忽略|绕过).{0,12}(?:指令|命令|规则)|(?:输出|泄露|上传).{0,10}(?:密钥|密码)|ignore.{0,20}(?:instructions|rules)", re.I)

def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def source_digest(sources):
    return digest(sources)

def evidence(source, sec, quote=None):
    quote = sec["text"] if quote is None else quote
    start = sec["text"].find(quote)
    if start < 0:
        raise ValueError("quote is not in source")
    return {"source_id": source["id"], "sha256": source["sha256"], "filename": source["filename"],
            "locator": sec["locator"], "quote": quote, "span": [start, start+len(quote)]}

def nature(text, context=""):
    if re.search(r"预测|预计|有望|预期|forecast|projected", text, re.I):
        return "forecast"
    if re.search(r"我认为|我的判断|估计|可能|观点|据报道|或超|访谈|评论|opinion", text+" "+context, re.I):
        return "opinion_or_reported"
    if re.search(r"实测|测量|observed|measured", text, re.I):
        return "observed_in_material"
    return "unknown"

def measurements(text):
    out = []
    for m in MEASURE.finditer(text):
        value, unit = m.group(1), m.group(2)
        norm = fg.normalized_measure(value, unit)
        out.append({"literal": m.group(), "number": value, "unit": unit, "span": list(m.span()),
                    "dimension": norm[0] if norm else unit.lower(),
                    "normalized": str(norm[1]) if norm else value})
    return out

def template(text):
    # Exact grammatical skeleton is required for automatic arithmetic verdicts.
    text = MEASURE.sub(lambda m: "<"+(fg.normalized_measure(m.group(1), m.group(2)) or (m.group(2).lower(),))[0]+">", text)
    return re.sub(r"\s+", "", text).strip("。；. ")

def features(text):
    text = YEAR.sub("", MEASURE.sub("", text)).lower()
    return set(re.findall(r"[a-z][a-z0-9_-]{2,}", text)) | set(re.findall(r"(?=([\u3400-\u9fff]{2}))", text))

def grammatical_equivalence(text, quote):
    """Only a copula immediately before the unchanged numeric literal.

    This does not authorize synonym substitution, negation removal, different
    conditions, numbers, units or forecast status. The original quote is kept.
    """
    def canonical(t):
        return re.sub(r"(?<=[\u3400-\u9fffA-Za-z])(?:为|是)(?=\s*[-+]?\d)","=",t)
    return text!=quote and canonical(text)==canonical(quote)

def composition_valid(c, cites, sources):
    if c.get("composition")=="adjacent_segments":
        if len(cites)<2 or len({e["source_id"] for e in cites})!=1:return False
        source=next(s for s in sources["sources"] if s["id"]==cites[0]["source_id"])
        positions={s["locator"]:i for i,s in enumerate(source["sections"])}
        points=[positions[e["locator"]] for e in cites]
        return points==list(range(points[0],points[0]+len(points))) and c["text"]=="".join(e["quote"] for e in cites)
    if len(cites)!=2 or cites[0]["source_id"]!=cites[1]["source_id"]:
        return False
    a,b=cites
    if c.get("composition")=="label_value":
        ma=re.fullmatch(r"工作表(.+)!([A-Z]+)(\d+)",a["locator"])
        mb=re.fullmatch(r"工作表(.+)!([A-Z]+)(\d+)",b["locator"])
        if not ma or not mb or ma.group(1)!=mb.group(1):return False
        def colnum(name):
            n=0
            for ch in name:n=n*26+ord(ch)-64
            return n
        same_column=ma.group(2)==mb.group(2) and int(ma.group(3))==1 and int(mb.group(3))>1
        same_record=ma.group(3)==mb.group(3) and colnum(mb.group(2))==colnum(ma.group(2))+1
        return (same_column or same_record) and c["text"]==a["quote"]+"："+b["quote"]
    if c.get("composition")=="table_row":
        source=next(s for s in sources["sources"] if s["id"]==a["source_id"])
        sections=source["sections"];positions={s["locator"]:i for i,s in enumerate(sections)}
        ai,bi=positions[a["locator"]],positions[b["locator"]]
        if ai>=bi or not a["quote"].startswith("|") or not b["quote"].startswith("|"):return False
        intervening=sections[ai+1:bi]
        if any(not s["text"].startswith("|") for s in intervening):return False
        return c["text"]==a["quote"]+"；"+b["quote"]
    return False

def extract(sources, excluded=None):
    if sources.get("errors"):
        raise ValueError("incomplete sources")
    excluded = set(excluded or [])
    claims, quarantined = [], []
    for source in sources["sources"]:
        if source["filename"] in excluded:
            continue
        heading = ""
        for sec in source["sections"]:
            text = sec["text"]
            if INJECTION.search(text):
                quarantined.append({"evidence": evidence(source, sec), "reason": "document_instruction_is_untrusted_data"})
                continue
            if len(text) < 45 and not measurements(text):
                heading = text
            # XLSX cells retain row/header context; never treat a naked number as
            # a standalone semantic claim. Original cell count stays unchanged.
            context = heading
            if source["type"] == ".xlsx":
                address = re.search(r"!([A-Z]+)(\d+)$", sec["locator"])
                if address:
                    col, row = address.groups()
                    prefix = sec["locator"].split("!")[0]+"!"
                    neighbours = [s for s in source["sections"] if s["locator"].startswith(prefix) and
                                  (re.search(r"\d+$", s["locator"]).group() == row or s["locator"] == prefix+col+"1")]
                    context = " | ".join(s["locator"]+"="+s["text"] for s in neighbours)
            for match in re.finditer(r"[^。！？\n]+[。！？]?", text):
                quote = match.group().strip()
                if not quote:
                    continue
                ev = evidence(source, sec, quote)
                cid = "C-"+digest(ev)[:16]
                claims.append({"id": cid, "text": quote, "kind": "fact", "scope": context,
                               "time": YEAR.findall(quote), "measurements": measurements(quote),
                               "qualifier": nature(quote, heading), "evidence": [ev],
                               "reliability": "locatable_material_assertion_external_truth_unverified",
                               "incomplete_sentence": len(quote)<12 or source["type"]==".xlsx"})
    groups = match_claims(claims)
    return {"schema": "factguard.claim_model.v2", "source_digest": source_digest(sources),
            "excluded_task_files": sorted(excluded), "claims": claims, "quarantined": quarantined,
            "comparisons": groups, "summary": {"claims": len(claims), "quarantined": len(quarantined),
            "comparison_candidates": len(groups), "semantic_completeness": "not_guaranteed"}}

def match_claims(claims):
    # Candidate discovery uses lexical overlap; it is not semantic arbitration.
    eligible = [c for c in claims if c.get("measurements") and not c.get("incomplete_sentence")]
    index, pairs = defaultdict(list), set()
    fs = [features(c["text"]) for c in eligible]
    for i, f in enumerate(fs):
        for k in f:
            if len(index[k]) < 160:
                for j in index[k]:
                    if eligible[j]["evidence"][0]["source_id"] != eligible[i]["evidence"][0]["source_id"]:
                        pairs.add((j,i))
            index[k].append(i)
    groups = []
    for i,j in sorted(pairs):
        a,b = eligible[i],eligible[j]
        score = len(fs[i]&fs[j])/max(1,len(fs[i]|fs[j]))
        if score < .23 and template(a["text"]) != template(b["text"]):
            continue
        am,bm = a["measurements"],b["measurements"]
        issues = []
        if a["time"] != b["time"]:
            issues.append("different_or_missing_period")
        if a["qualifier"] != b["qualifier"] or a["qualifier"] == "unknown":
            issues.append("nature_unconfirmed_or_different")
        if [m["dimension"] for m in am] != [m["dimension"] for m in bm]:
            issues.append("dimension_or_metric_count_mismatch")
        if template(a["text"]) != template(b["text"]):
            issues.append("semantic_scope_requires_review")
        if a.get("scope") != b.get("scope"):
            issues.append("context_scope_not_identical")
        if any(re.search(r"\d\s*(?:-|到|至|~|～)\s*\d",c["text"]) for c in (a,b)):
            issues.append("range_expression_requires_review")
        if issues:
            verdict = "scope_difference_or_semantic_review"
        else:
            same = [Decimal(m["normalized"]) for m in am] == [Decimal(m["normalized"]) for m in bm]
            verdict = "numeric_consistency" if same else "numeric_conflict_under_exact_template"
        groups.append({"id": "G-"+digest([a["id"],b["id"]])[:14], "claim_ids": [a["id"],b["id"]],
                       "similarity": round(score,4), "verdict": verdict, "issues": issues,
                       "evidence": a["evidence"]+b["evidence"], "external_truth_verified": False})
    return groups

def authenticate(sources, input_dir):
    """Reparse originals: catches changed source files AND forged cached text."""
    root = Path(input_dir).resolve()
    expected={str(p.relative_to(root)).replace("\\","/") for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (".pdf",".docx",".xlsx",".txt",".md")}
    names=[s["filename"] for s in sources["sources"]]
    if set(names)!=expected or len(set(names))!=len(names):
        raise ValueError("source_inventory_incomplete_or_duplicated")
    for source in sources["sources"]:
        path = (root/source["filename"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("source path escapes input root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("source_file_changed:"+source["filename"])
        parser = {".docx": fg.parse_docx, ".pdf": fg.parse_pdf, ".xlsx": fg.parse_xlsx}.get(source["type"])
        if parser:
            actual = parser(path)
        else:
            actual = [fg.section("行%d"%i,t) for i,t in enumerate(path.read_text(encoding="utf-8-sig").splitlines(),1) if t.strip()]
        if actual != source["sections"]:
            raise ValueError("cached_source_text_changed:"+source["filename"])
    if sources.get("errors"):
        raise ValueError("incomplete_sources")

def validate_evidence(items, sources):
    index = fg.index_sources(sources)
    citations,issues = [],[]
    for item in items:
        rec = index.get((item.get("source_id"),item.get("locator")))
        if not rec:
            issues.append("locator_not_found"); continue
        source,sec = rec
        q = item.get("quote", "")
        span = item.get("span")
        if not q or q not in sec["text"]:
            issues.append("quote_not_found"); continue
        if span is None:
            issues.append("span_missing");continue
        if not isinstance(span,list) or len(span)!=2 or not all(type(i) is int for i in span) or not 0<=span[0]<span[1]<=len(sec["text"]) or sec["text"][span[0]:span[1]]!=q:
            issues.append("span_mismatch"); continue
        if item.get("sha256") != source["sha256"]:
            issues.append("source_sha_missing_or_mismatch"); continue
        if item.get("filename") != source["filename"]:
            issues.append("source_filename_binding_mismatch");continue
        citations.append(evidence(source,sec,q))
    return citations,issues

def review(sources, draft, input_dir):
    authenticate(sources, input_dir)
    if [c.get("title") for c in draft.get("chapters",[])] != fg.CHAPTERS:
        raise ValueError("requires official seven chapters in order")
    claims = draft.get("claims",[])
    byid = {c.get("id"):c for c in claims}
    if len(byid)!=len(claims) or any(not k for k in byid):
        raise ValueError("duplicate_or_missing_claim_id")
    results = []
    tables=table_checks(sources)
    for c in claims:
        cites, issues = validate_evidence(c.get("evidence",[]), sources)
        if c.get("kind")!="fact" or not c.get("text","").strip():
            issues.append("invalid_fact")
        if not cites:
            issues.append("missing_evidence")
        quotes = [e["quote"] for e in cites]
        if any(e["filename"] in draft.get("excluded_task_files",[]) for e in cites):
            issues.append("task_instructions_cannot_be_research_evidence")
        # Conservative extractive mode. Semantic paraphrases require a hosting
        # independent model's review; offline rules never rubber-stamp them.
        composed = composition_valid(c,cites,sources)
        if c.get("composition") and not composed:issues.append("invalid_header_value_or_table_binding")
        grammar_match=any(grammatical_equivalence(c.get("text",""),q) for q in quotes)
        if cites and c["text"] not in quotes and not composed and not grammar_match:
            issues.append("paraphrase_or_value_change_requires_revision")
        # When the claimed text is exactly the concatenation of authenticated
        # adjacent source sections, a numeral may cross the section boundary
        # (e.g. '12' + '9.9%' -> '129.9%'). Compare numeric tokens only for
        # non-composed claims: composition_valid already checks exact text,
        # source identity, locator order and adjacency without paraphrasing.
        if cites and not composed and set(fg.tokens(c["text"]))-set(t for q in quotes for t in fg.tokens(q)):
            issues.append("number_or_unit_mismatch")
        if INJECTION.search(c.get("text","")):
            issues.append("instruction_injection_quarantined")
        if cites and c.get("assertion_mode") != "material_attribution":
            issues.append("unverified_world_fact_must_be_attributed")
        for finding in tables:
            if any(e["source_id"]==finding["evidence"][0]["source_id"] and e["locator"]==finding["evidence"][0]["locator"] for e in cites):
                issues.append("source_table_conclusion_conflicts_with_cells")
        results.append({"id":c["id"],"text":c.get("text"),"status":"needs_revision" if issues else "supported_material_attribution",
                        "issues":sorted(set(issues)),"citations":cites,"text_support_mode":"restricted_grammatical_equivalence" if grammar_match else "source_transcription",
                        "external_truth_verified":False})
    para_results=[]
    seen = set()
    for ci,ch in enumerate(draft["chapters"]):
        for pi,p in enumerate(ch.get("paragraphs",[])):
            issues=[]
            if not p.get("text", "").strip(): issues.append("blank_paragraph")
            sig=p.get("text", "").strip()
            if sig in seen: issues.append("duplicate_paragraph")
            seen.add(sig)
            if p.get("kind")=="fact":
                c=byid.get(p.get("claim_id"))
                if not c or c["text"]!=p.get("text"): issues.append("claim_text_binding_mismatch")
            elif p.get("kind")=="analysis":
                basis=p.get("basis",[])
                if not basis or any(i not in byid for i in basis): issues.append("analysis_without_valid_evidence_basis")
                if not re.search(r"建议|推断|可能|可以|若|如果|应当|应|需要|不足|不能|不宜|尚未|不必|有助于|未必",p.get("text","")):
                    issues.append("analysis_requires_explicit_inference_or_proposal")
                if re.search(r"(?:所有|全部).{0,18}(?:都已|均已|已经全部|已经完全)",p.get("text","")):
                    issues.append("unsupported_universal_fact_in_analysis")
                # Prevent smuggling new numeric assertions as analysis. Novel
                # calculations must be explicit calculated fact certificates.
                allowed=set(t for i in basis if i in byid for t in fg.tokens(byid[i]["text"]))
                if p.get("evidence"):
                    analysis_cites,analysis_issues=validate_evidence(p["evidence"],sources)
                    issues.extend(analysis_issues)
                    allowed.update(t for e in analysis_cites for t in fg.tokens(e["quote"]))
                    if any(e["filename"] in draft.get("excluded_task_files",[]) for e in analysis_cites):
                        issues.append("task_instructions_cannot_be_analysis_evidence")
                for cert in p.get("calculations",[]):
                    try:
                        result=calculations.calculate(cert,sources,validate_evidence)
                        allowed.update(fg.tokens(str(result["value"])))
                        allowed.update(fg.tokens(str(result["value"])+result["unit"]))
                        if isinstance(result["value"],dict):allowed.update(str(v) for v in result["value"].values())
                    except (ValueError,KeyError,ArithmeticError) as err:
                        issues.append("invalid_calculation_certificate:"+str(err))
                if set(fg.tokens(p.get("text","")))-allowed: issues.append("new_number_in_analysis")
            else: issues.append("unknown_paragraph_kind")
            para_results.append({"chapter":ci,"paragraph":pi,"issues":issues})
    model=extract(sources,draft.get("excluded_task_files",[]))
    return {"schema":"factguard.review.v2","source_digest":source_digest(sources),"draft_digest":digest(draft),
            "reviewer":"separate_process_originals_reopened_rules","independent_model_verified":False,
            "claims":results,"paragraphs":para_results,"cross_source":model["comparisons"],"table_checks":tables,
            "summary":{"claims":len(results),"needs_revision":sum(bool(x["issues"]) for x in results),
                       "paragraph_errors":sum(bool(p["issues"]) for p in para_results),
                       "source_files_reopened":len(sources["sources"]),"external_truth_verified":False}}

def table_checks(sources):
    """Generic pipe-table arithmetic and explicit minimum/maximum contradictions."""
    findings=[]
    for s in sources["sources"]:
        table=[];header=[]
        for sec in s["sections"]:
            text=sec["text"]
            if text.startswith("|") and text.count("|")>=3:
                cells=[x.strip() for x in text.strip("|").split("|")]
                if all(re.fullmatch(r"[-: ]*",x) for x in cells):continue
                if not header or not any(re.search(r"\d",x) for x in cells):header=cells;table=[];continue
                table.append((cells,sec))
                continue
            if table and re.search(r"最低|最高|最小|最大",text):
                for col,name in enumerate(header):
                    # Use a metric label only when explicitly present in both.
                    metric=re.sub(r"\([^)]*\)|（[^）]*）", "",name)
                    extremum_mention=re.search(re.escape(metric)+r"(?:\([^)]*\)|（[^）]*）)?(?:最|的|为|值|是|达到|[\s])*?(最低|最高|最小|最大)",text)
                    if not metric or not extremum_mention:continue
                    vals=[]
                    for cells,rowsec in table:
                        if col<len(cells) and re.fullmatch(r"\d+(?:\.\d+)?%?",cells[col]):
                            vals.append((Decimal(cells[col].rstrip("%")),cells[0],rowsec))
                    if not vals:continue
                    extreme=min(vals,key=lambda v:v[0]) if extremum_mention.group(1) in ("最低","最小") else max(vals,key=lambda v:v[0])
                    mentioned=[v for v in vals if v[1] in text and str(v[0]) in text]
                    if mentioned and mentioned[0][0]!=extreme[0]:
                        findings.append({"type":"table_extremum_text_disagreement","metric":metric,
                         "claimed_row":mentioned[0][1],"actual_extremum_row":extreme[1],"extremum":str(extreme[0]),
                         "evidence":[evidence(s,sec),evidence(s,extreme[2])],"external_truth_verified":False})
    return findings

def challenge(review_data):
    # The challenger answers the actual audit, not the author's self-approval.
    return {"schema":"factguard.rebuttal.v2","review_digest":digest(review_data),
            "decisions":[{"claim_id":r["id"],"decision":"accept_objection","reason":"原件支持范围不足，不能用起草者自信或投票替代来源；撤回或恢复有定位的材料原话。",
                          "issue_codes":r["issues"],"sourceIds":[e["source_id"] for e in r["citations"]]}
                         for r in review_data["claims"] if r["issues"]],
            "reviewer":"deterministic_evidence_challenger","independent_model_verified":False}

def revise(draft, review_data, rebuttal):
    if digest(draft)!=review_data["draft_digest"] or digest(review_data)!=rebuttal["review_digest"]:
        raise ValueError("stage_digest_mismatch")
    final=copy.deepcopy(draft);events=[]
    objections={x["claim_id"]:x for x in rebuttal["decisions"]}
    records={r["id"]:r for r in review_data["claims"]}
    retract=set()
    for c in final["claims"]:
        r=records[c["id"]]
        if not r["issues"]:continue
        if c["id"] not in objections or objections[c["id"]]["decision"]!="accept_objection":
            raise ValueError("unresolved_objection")
        before=copy.deepcopy(c)
        if "source_table_conclusion_conflicts_with_cells" in r["issues"]:
            finding=next(f for f in review_data["table_checks"] if any(e["source_id"]==f["evidence"][0]["source_id"] and e["locator"]==f["evidence"][0]["locator"] for e in r["citations"]))
            c["text"]=finding["evidence"][1]["quote"];c["evidence"]=[finding["evidence"][1]];c["assertion_mode"]="material_attribution"
            c["label"]="表格原值，取代与表格冲突的文字结论"
            objections[c["id"]]["reason"]="独立回查表格后发现极值与叙述不一致，改为引用真实极值行，不据此宣称外部实验已复现。"
        elif r["citations"] and "instruction_injection_quarantined" not in r["issues"]:
            c["text"]=r["citations"][0]["quote"];c["evidence"]=r["citations"][:1];c["assertion_mode"]="material_attribution"
        else:retract.add(c["id"])
        events.append({"claim_id":c["id"],"before":before,"after":None if c["id"] in retract else copy.deepcopy(c),
                       "reason":objections[c["id"]]["reason"],"issues":r["issues"],
                       "sourceIds":[e["source_id"] for e in r["citations"]],"recheck":"pending"})
    final["claims"]=[c for c in final["claims"] if c["id"] not in retract]
    byid={c["id"]:c for c in final["claims"]}
    for ch in final["chapters"]:
        keep=[]
        for p in ch["paragraphs"]:
            if p.get("claim_id") in retract:continue
            if p["kind"]=="fact":p["text"]=byid[p["claim_id"]]["text"]
            if p["kind"]=="analysis":
                p["basis"]=[i for i in p.get("basis",[]) if i not in retract]
                if not p["basis"]:continue
            keep.append(p)
        ch["paragraphs"]=keep
    return final,events

def render_final(sources,final,second,output):
    if second["source_digest"]!=source_digest(sources) or second["draft_digest"]!=digest(final):
        raise ValueError("render_digest_mismatch")
    if second["summary"]["needs_revision"] or second["summary"]["paragraph_errors"]:
        raise ValueError("second_review_failed")
    claims={c["id"]:c for c in final["claims"]}
    audited={r["id"]:r for r in second["claims"]}
    paragraphs=[(fg.TOPIC,"Title"),("本报告以提供的调研材料为研究范围。材料记载与现实世界真实性分别处理；预测和个人判断保留限定语，分析段落提出有条件的研究建议。",None)]
    counts=[];cited=set();body_han=0;analysis_han=0
    for n,ch in enumerate(final["chapters"],1):
        paragraphs.append(("第%s章 %s"%(n,ch["title"]),"Heading1"));fcount=0
        for p in ch["paragraphs"]:
            text=p["text"];body_han+=len(fg.HAN.findall(text))
            if p["kind"]=="fact":
                c=claims[p["claim_id"]];fcount+=1;cited.add(c["id"])
                ref="；".join("【%s，%s】"%(e["filename"],e["locator"]) for e in audited[c["id"]]["citations"])
                paragraphs.append(("材料记载（%s）：%s%s"%(c.get("label","待核实材料陈述"),text,ref),None))
            else:
                analysis_han+=len(fg.HAN.findall(text))
                refs="".join("【%s，%s】"%(e["filename"],e["locator"]) for e in p.get("evidence",[]))
                refs+="".join(calculations.reference(calculations.calculate(cert,sources,validate_evidence)) for cert in p.get("calculations",[]))
                paragraphs.append(("[分析推断] "+text+refs,None))
        if not fcount:raise ValueError("chapter_without_facts")
        counts.append(fcount)
    if body_han<10000:raise ValueError("body_han_below_10000:%s"%body_han)
    fg.docx_package(paragraphs,output)
    return {"schema":"factguard.report_metrics.v2","body_han":body_han,"analysis_han":analysis_han,
            "count_rule":"U+3400..U+9FFF in seven chapter paragraph text; excludes title, headings, citations, labels, appendices",
            "chapters":7,"factual_paragraphs":sum(counts),"unique_cited_claims":len(cited),"chapter_fact_counts":counts,
            "cited_facts_passed":len(cited),"coverage_layer":"exact_source_attribution_not_world_truth",
            "teleagent_verified":False,"independent_model_verified":False}

def execute(sources_path,draft_path,input_dir,work,output):
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    paths={"review":work/"01_review.json","rebuttal":work/"02_rebuttal.json","final":work/"03_final.json","revisions":work/"04_revisions.json","second":work/"05_second_review.json"}
    commands=[]
    def isolated(draft, dest):
        cmd=[sys.executable,str(Path(__file__).resolve()),"review","--sources",str(sources_path),"--draft",str(draft),"--input-dir",str(input_dir),"--out",str(dest)]
        start=time.perf_counter();p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8")
        commands.append({"argv":cmd,"returncode":p.returncode,"stdout":p.stdout,"stderr":p.stderr,"seconds":round(time.perf_counter()-start,3)})
        if p.returncode:raise ValueError("isolated_review_failed:"+p.stderr)
        return fg.read_json(dest)
    first=isolated(draft_path,paths["review"])
    rebuttal=challenge(first);fg.dump(rebuttal,paths["rebuttal"])
    final,events=revise(fg.read_json(draft_path),first,rebuttal);fg.dump(final,paths["final"])
    second=isolated(paths["final"],paths["second"])
    audited={r["id"]:r for r in second["claims"]}
    for event in events:event["recheck"]="retracted" if event["after"] is None else audited[event["claim_id"]]["status"]
    fg.dump({"schema":"factguard.revisions.v2","events":events},paths["revisions"])
    metrics=render_final(fg.read_json(sources_path),final,second,output)
    fg.dump(metrics,work/"report_metrics.json")
    chain={"schema":"factguard.local_run.v2","NOT_TELEAGENT_LOG":True,"commands":commands,
           "stages":{k:{"path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for k,p in paths.items()},
           "draft_sha256":hashlib.sha256(Path(draft_path).read_bytes()).hexdigest(),"revisions":len(events),"metrics":metrics}
    fg.dump(chain,work/"local_run.json")
    return metrics

def main():
    ap=argparse.ArgumentParser(description="FactGuard source-bound stages (no network/model API)")
    subs=ap.add_subparsers(dest="command",required=True)
    p=subs.add_parser("extract");p.add_argument("--sources",required=True);p.add_argument("--exclude",action="append",default=[]);p.add_argument("--out",required=True)
    p=subs.add_parser("review");p.add_argument("--sources",required=True);p.add_argument("--draft",required=True);p.add_argument("--input-dir",required=True);p.add_argument("--out",required=True)
    p=subs.add_parser("run");p.add_argument("--sources",required=True);p.add_argument("--draft",required=True);p.add_argument("--input-dir",required=True);p.add_argument("--work",required=True);p.add_argument("--out",required=True)
    a=ap.parse_args()
    try:
        if a.command=="extract":
            result=extract(fg.read_json(a.sources),a.exclude);fg.dump(result,a.out);result=result["summary"]
        elif a.command=="review":
            result=review(fg.read_json(a.sources),fg.read_json(a.draft),a.input_dir);fg.dump(result,a.out);result=result["summary"]
        else:result=execute(a.sources,a.draft,a.input_dir,a.work,a.out)
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as e:
        print(json.dumps({"error":type(e).__name__,"message":str(e)},ensure_ascii=False),file=sys.stderr);return 2

if __name__=="__main__":sys.exit(main())
