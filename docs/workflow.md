# Agent workflow

1. Understand the requested decision, scope, object grain, time window and output.
2. Inspect only the relevant source directory and known health/version metadata. Keep availability, applicability and freshness separate. Do not connect to every source merely to fill a form.
3. Register a case. For complex work, create execution.json with an analysis_brief: version=1, positive revision, decision, risk, evidence_plan, stop_condition, revision_reason, and sources. Each source has source_id, kind, availability (available/not_configured/inaccessible/unknown), applicability (matched/mismatch/unknown), freshness (current/stale/unknown), and note. Reuse request/scope/tasks/routes rather than duplicating them.
4. Run plan checks with --require-case --require-brief. Select tools after the brief; revise the brief when evidence changes scope or interpretation. Simple questions remain lightweight.
5. Gather scoped evidence. Distinguish code implementation, deployed versions, configuration snapshots, observed data, documented intent and inference. Failed queries are not zero results; a request record is not necessarily a user action. Use only host-authorized read-only access.
6. Verify references, arithmetic, scope and counterexamples. Program checks validate structure; the agent must inspect meaning. A correct citation can still support an incorrect claim.
7. Deliver conclusions, explanations, limitations and actionable recommendations. Mark partial/unknown outcomes honestly. Keep personal data and credentials out of evidence summaries.
8. Capture the report, seal execution, and finish the case. Delivery and approval are independent. Do not invent receipts. Keep prior snapshots.
9. Reuse evidence only within its version, scope and freshness. Source changes request review, not an automatic declaration that a claim is false. Revalidation currently requires explicit agent action.
10. Record reusable methods separately from host business facts. Do not promote speculative conclusions into approved knowledge.

Optional upstreams: CodeGraph for code relations; codebase-reverse for coverage; Reversa for specifications; DDS for citation fidelity; SDD-Pro for database objects; DeepWiki-Open for repository maps; RAGFlow for retrieval experiments. Inspect pinned resources and licenses, record actual usage stages, and fall back explicitly when unavailable. They are not required for core package operation.
