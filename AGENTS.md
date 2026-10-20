# Repository agent rules

## Canonical repository

- The sole working remote is `https://github.com/pbw1212/nlp-` (`origin`); the default branch is `main`.
- Before any Git network operation, verify the repository root, remotes, branch, and status. Follow [`docs/repository-policy.md`](docs/repository-policy.md); stop if `origin` does not match exactly.
- Do not infer a remote from the project or checkout name. Dated maintenance and publication records are historical evidence, not current routing instructions.

## Semantic behavior must not be hardcoded

- Do not implement task intent detection, task-stage detection, memory extraction,
  memory distillation, retrieval decisions, context selection, or conflict resolution
  with keyword lists, regular-expression trigger tables, phrase matching, or other
  locally hardcoded semantic rules.
- Use the configured Provider to propose semantic interpretations through a typed,
  versioned structured response. Provider output is only a proposal and must never
  mutate durable state directly.
- Keep deterministic code limited to schema/type validation, provenance and source
  authority checks, ID ownership, lifecycle invariants, bounded resource handling,
  transactionality, and explicit user confirmation when the Provider is uncertain.
- If the Provider cannot identify a target or replacement with sufficient grounding,
  preserve the uncertainty and request confirmation. Never reconstruct missing semantic
  content from trigger words or from a guessed fallback.
- Natural-language explicit memory requests must use the Provider's typed request analysis
  and a durable Task 1 write result. The Task 3 proposal must remain empty for that route;
  a successful write is the only basis for acknowledging that the memory was saved.

Any future change that appears to need a semantic rule must first change the typed
Provider contract or introduce an evaluated model-backed mechanism; do not add a local
keyword shortcut.
