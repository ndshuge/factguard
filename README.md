# FactGuard — Source-grounded Research Review Skill

FactGuard is a Python skill for inspecting source documents, structuring claims, auditing citations and numeric/context consistency, and producing traceable research-review artifacts.

## Included

- skill-package/SKILL.md: skill contract and safety/evidence gates.
- skill-package/scripts/: source inspection, claim auditing, comparison and report pipeline.
- skill-package/references/contracts.md: input/output contracts.
- tests/: core offline regression tests.
- demo/index.html: small static product preview.

## Quick start

Use Python 3.10 or newer. Some PDF input workflows require PyMuPDF (pip install pymupdf); DOCX/XLSX processing uses standard-library ZIP/XML parsing where applicable.

From the skill-package directory, run:
    python scripts/factguard.py --help

Run core tests from the repository root:
    $env:PYTHONPATH = "skill-package/scripts"
    python -m unittest discover -s tests -v

Consult skill-package/SKILL.md before running a full review. The workflow must preserve source files, exact citations, warnings, and unresolved conflicts.

## Evidence and limitations

- This repository contains product source code only; no competition PDF/video, TeleAgent logs, private snapshots, user datasets, API keys, or local machine credentials are included.
- Offline tests do not establish official platform installation or external-world truth.
- A source citation establishes where a claim came from, not that the source itself is true.
- Full reports require host-side independent review and required evidence gates; the offline sample is not a substitute for official acceptance.
