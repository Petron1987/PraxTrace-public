# Architecture

The host agent owns reasoning and report synthesis. PraxTrace owns explicit records, reference checks, snapshots and reusable tools. Hosts supply code, deployment, configuration, read-only data and document access. Credentials and private data remain outside this package.

Flow: question -> scoped source preflight -> revisable analysis brief -> evidence collection -> deterministic checks plus agent semantic review -> versioned delivery -> explicit revalidation.

Implemented modules: execution_record, case_records/case_ops/case_search, evidence_review, citation_check, source_inventory, route_map, rule_lookup, compare_periods, product_upstream and upstream_usage. Modules use package-relative imports; no host checkout is imported.

Evidence, claims, freshness and approval are separate concepts. Current records support citations, hashes and explicit review status. A general source registry, automated health checks and dependency-driven claim invalidation are planned, not implemented. The package is a toolkit, not a general agent runtime or a database permission boundary.
