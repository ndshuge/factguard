# FactGuard v2 contracts and stage boundaries

Runtime: Python 3.10+; standard library for DOCX/XLSX/TXT; PyMuPDF for PDF. Dependencies are optional by input type. Missing PDF text, empty pages or formula caches produce visible failure. No installs, network calls, model endpoints or credentials are performed by the Skill scripts.

## Sources and claims
Run `factguard.py inspect`. Pass its sources.json unchanged. It contains all supported input files including any explicitly excluded task specification. Exclusion affects claim extraction, not source inventory authentication.

Source `id` hashes content SHA and relative filename, so copies in separate paths remain distinct. Each evidence entry must have source_id, sha256, filename, locator, quote and span `[start,end)` in Python Unicode code points in the extracted section. PDF locators refer to page and extracted block, Word to XML body paragraph/table cell, Excel to worksheet and address. PDF fragments can split a sentence; cite multiple original fragments or another complete supplied source, never pretend a fragment is a complete statement.

An extraction claim includes id, text, scope (heuristic heading or row context), time (literal years), measurements (literal value/unit/dimension/normalized/span), qualifier, evidence and reliability. These fields are candidate annotations, not a comprehensive semantic ontology. Incomplete numeric cells need their header and row context.

## Draft supplied by the hosting agent
```json
{
  "schema":"factguard.draft.v2",
  "excluded_task_files":["task.docx"],
  "claims":[{
    "id":"claim-1", "kind":"fact", "text":"Exact source sentence.",
    "assertion_mode":"material_attribution", "label":"source assertion",
    "evidence":[{"source_id":"SOURCE_ID","sha256":"FULL_SHA","filename":"paper.docx",
      "locator":"段落0002","quote":"Exact source sentence.","span":[0,22]}]
  }],
  "chapters":[{"title":"行业概述","paragraphs":[
    {"kind":"fact","text":"Exact source sentence.","claim_id":"claim-1"},
    {"kind":"analysis","text":"An explicitly tentative interpretation.","basis":["claim-1"]}
  ]}]
}
```
The snippet shows one chapter only; an actual run requires all seven official chapters in order. Fill spans from the source index, not the illustrative number. Never use the task specification's examples as research evidence.

`composition:"label_value"` permits text exactly equal to two validated source quotes joined by a Chinese colon, for a header/value pair from the same source. This is source transcription, not an arithmetic formula. Derived calculations should be separately certified against referenced cells and clearly described as calculations; do not claim they are original quotations.

Each factual paragraph binds to the exact reviewed claim text. Each analysis paragraph needs existing claim IDs, is labeled on output, and cannot introduce new numeric tokens. Semantic analysis still needs host editorial review: numeric checking cannot prove every prose inference.

## Independent review, challenge, revision and recheck
`pipeline.py review` has only originals, source index and draft as input. No author approval or rationale is accepted. `pipeline.py run` starts review in a new Python process and preserves all artifacts:

- 01_review.json: source and draft digests, source objections, paragraph issues, automatically discovered comparisons, table conflicts.
- 02_rebuttal.json: each objection must be accepted or supported with verifiable counterevidence; the offline challenger conservatively accepts evidence objections. No votes.
- 03_final.json: revised draft; unsupported claims removed; wrong values restored to real quotations; contradictory extrema replaced with the original extremum row.
- 04_revisions.json: before/after/reason/sourceIds/issues/recheck for every changed claim.
- 05_second_review.json: a fresh independent process reopens originals and audits the revised artifact.
- report_metrics.json: Han count, fact counts, count rule, traceability layer and unverified platform/model flags.
- local_run.json: actual subprocess argv, exit codes, runtime and file hashes. This is not a TeleAgent conversation log.

New facts added by revision require another source review. Missing chapters, duplicate paragraphs, unsupported facts, stale digest or insufficient body length fail the publishing gate. The count uses Han code points U+3400..U+9FFF only in chapter paragraph text, excluding titles, chapter headings, citation strings, status labels and appendices. This is deliberately stricter than total Word text count.

## Host-side semantic review and TeleAgent gate
For interpretations, scope disputes or conclusions not covered by rules, use a genuinely separate reviewing context when available. Give it the originals and claims, without the author's intended answer. Save actual model/agent invocations if performed. With only phase/process isolation, report that limitation accurately and retain disputes as uncertain material assertions.

Official log.json must be exported from the authorized real TeleAgent primary conversation, byte-for-byte unchanged. Import, account login, paid APIs, external upload and submission require user authorization. A missing log blocks formal submission, not offline core development. Never synthesize a log or imply hidden B results.
