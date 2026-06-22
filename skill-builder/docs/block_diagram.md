# Skill-Builder — Block Diagram

Design-time policy compiler: a policy document is compiled into versioned, human-approved
runtime rules. LLMs are used only at design time; the runtime is deterministic.

```
                            ┌─────────────────────────────────────┐
   INPUT                    │   Policy document                   │
   ─────                    │   .md / .txt / .docx / .pdf  OR text │
                            └──────────────────┬──────────────────┘
                                               │
╔══════════════════════════════════════════════▼══════════════════════════════════════╗
║  DETERMINISTIC TEXT PIPELINE                                                           ║
║                                                                                        ║
║   ┌───────────┐    ┌────────────┐    ┌────────────┐                                    ║
║   │  Extract  │──▶ │ Normalize  │──▶ │  Segment   │──▶ clauses[]                       ║
║   │ extract.py│    │normalize.py│    │ segment.py │   (source_text, type,              ║
║   │ file→text │    │→ canonical │    │ → atomic   │    section_path, page)             ║
║   └───────────┘    │  markdown +│    │  clauses + │                                    ║
║                    │  sections  │    │  clause_type                                    ║
║                    └────────────┘    └────────────┘                                    ║
╚════════════════════════════════════════════════════════════════════════╤══════════════╝
                                                                           │ clauses
╔══════════════════════════════════════════════════════════════════════════▼══════════════╗
║  UNDERSTANDING (LLM-ASSISTED, heuristic fallback)        ┌─────────────────────────┐     ║
║                                                          │  Domain Pack            │     ║
║   ┌────────────┐    ┌─────────────┐    ┌──────────────┐  │  domain_packs/loader.py │     ║
║   │  Profile   │    │  Classify   │    │ Extract Atoms│  │  vocab: field/role/     │     ║
║   │profiler.py │──▶ │classifier.py│──▶ │  atoms.py    │  │  action → namespace     │     ║
║   │ doc shape, │    │ clause →    │    │ domain-neutral│  │  (column|param|metric|  │     ║
║   │ domain     │    │ disposition │    │ IR (audit)   │◀─┤   caller)               │     ║
║   └─────┬──────┘    └──────┬──────┘    └──────┬───────┘  └───────────┬─────────────┘     ║
║        profile          disposition         atoms[]                  │ (pre-check)        ║
╚═══════════╪════════════════╪══════════════════╪═════════════════════╪═══════════════════╝
            │                │                   │                     │
╔═══════════╪════════════════╪══════════════════╪═════════════════════╪═══════════════════╗
║  LLM RULE EXTRACTION  ◀── the ONLY required LLM step                 │                   ║
║                              ┌──────────────────────┐                │                   ║
║   clauses ──────────────────▶│  RuleExtractor       │── candidate_rules[]               ║
║                              │  llm.py              │   (flat §9 IR,                     ║
║   providers: nvidia(default),│  verbatim values,    │    review_status = PENDING)        ║
║   groq, deepseek, grok, openai│  no invented facts) │                                    ║
║                              └──────────┬───────────┘                                    ║
╚═════════════════════════════════════════╪═══════════════════════════════════════════════╝
                                          │ candidate rules (pending)
╔══════════════════════════════════════════▼══════════════════════════════════════════════╗
║  VALIDATION ENGINE  (deterministic, multi-pass)   validation/engine.py                   ║
║  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ ┌───────────┐ ┌────────────┐      ║
║  │ schema  │▶│ grounding│▶│ semantic │▶│ executability│▶│consistency│▶│testability │      ║
║  │ Pydantic│ │evidence ⊂│ │pack vocab│ │§9c BINDER    │ │conflicts. │ │tests_gen.py│      ║
║  │ enums   │ │ source   │ │ valid    │ │ pre-check    │ │py overlap │ │trigger+neg │      ║
║  └─────────┘ └──────────┘ └──────────┘ └──────────────┘ └───────────┘ └────────────┘      ║
║          │                                    │                                          ║
║          ▼                                    ▼                                          ║
║   ValidationReport                    UnresolvedItem[]  ◀── no silent drops              ║
║   (executable/testable/publishable)   unresolved.py        (unmappable_symbol, …)        ║
║                                                                                          ║
║   Clause Ledger (ledger.py): every clause → disposition → atoms → rules → unresolved     ║
╚════════════════════════════════════════════╤═════════════════════════════════════════════╝
                                             │
╔═════════════════════════════════════════════▼═══════════════════════════════════════════╗
║  HUMAN REVIEW & APPROVAL   (FastAPI /design/skills/… + React UI :5173)   api.py          ║
║                                                                                          ║
║   inspect: candidate-rules · validation-report · unresolved-items · clause-ledger        ║
║   actions: approve / reject / edit (PATCH) · resolve|waive unresolved                    ║
║                                                                                          ║
║   PUBLISH BLOCKED IF: open critical unresolved · any rule not approved · not publishable ║
╚════════════════════════════════════════════╤═════════════════════════════════════════════╝
                                             │ approved rules only
╔═════════════════════════════════════════════▼═══════════════════════════════════════════╗
║  PUBLISH  (deterministic)   artifacts.py  →  skills/<skill_id>/v<version>/               ║
║                                                                                          ║
║   PUBLISHED                            PER-RUN  (runs/<run_id>/)                          ║
║   • source_policy.md (immutable)       • document_profile.yaml                           ║
║   • policy_skill.yaml                  • clauses.yaml                                     ║
║   • extracted_rules.yaml (active only) • clause_ledger.yaml                              ║
║   • test_cases.yaml                    • policy_atoms.yaml                                ║
║   • review_report.yaml                 • unresolved_items.yaml / validation_report.yaml  ║
╚════════════════════════════════════════════╤═════════════════════════════════════════════╝
                                             │  extracted_rules.yaml
                                             ▼
                          ┌──────────────────────────────────────┐
                          │  semantic-layer binder (downstream)   │
                          │  publish-policy → policy.yaml         │
                          │  expects ZERO rejections              │
                          │  (executability pre-check guarantees) │
                          └──────────────────────────────────────┘

  Persistence: store.py (SQLite→Postgres) · Contracts: schema.py (Pydantic) · CLI: cli.py
```

## Reading it

- **Deterministic path:** Extract → Normalize → Segment, all of Validation, Ledger, and
  Publish run with no LLM.
- **LLM touches three boxes only:** Profile and Classify (both with heuristic fallback) and
  Extract Atoms are *optional*; **Extract Rules is the one required LLM step**, and
  everything it emits is a `pending` candidate.
- **The executability validator is the keystone** — it mirrors the semantic-layer's §9c
  binder, so any rule the runtime would reject is caught at design time → the downstream
  binder sees zero rejections.
- **Two guarantees enforced structurally:** *no silent drops* (every clause lands in the
  ledger with a disposition; failures become first-class `UnresolvedItem`s) and *nothing
  publishes without human approval*.
