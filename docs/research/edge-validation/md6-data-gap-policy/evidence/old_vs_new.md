# Old head versus new head: behavioural probe (SYNTHETIC)

Run of `old_vs_new_probe.py` (same file, unchanged) against PR #94 head `930af2aef829fa14064d0ad0be0cfa4bda12fbec` (old) and the Wave V9 head (new)
on 2026-10-29. `CONTROL` and `REGRESSION` rows are positive controls: they must not change. Rows marked `changed` are the intended fixes;
`F1-default` is deliberately unchanged (default semantics B, see EVALUATION-EXPOSURE-LINEAGE.md 5.5.1).

| Row | Finding | Scenario | Old head | New head | Changed |
|---|---|---|---|---|---|
| F2-whitespace | F-2 | declared set, authority/correction refs = 'whitespace', default (no verifier) | INDEPENDENT | UNVERIFIED | yes |
| F2-x | F-2 | declared set, authority/correction refs = 'x', default (no verifier) | INDEPENDENT | PENDING_QUANT_AUTHORITY | yes |
| F2-NONE | F-2 | declared set, authority/correction refs = 'NONE', default (no verifier) | INDEPENDENT | UNVERIFIED | yes |
| F2-tab | F-2 | declared set, authority/correction refs = 'tab', default (no verifier) | INDEPENDENT | UNVERIFIED | yes |
| F2-well-formed free text | F-2 | declared set, authority/correction refs = 'well-formed free text', default (no verifier) | INDEPENDENT | PENDING_QUANT_AUTHORITY | yes |
| F3-member | F-3 | declared member, disjoint footprint, cited alone (declaration before runs) | INDEPENDENT | EXPLORATORY | yes |
| F3-ctrl | CONTROL | undeclared disjoint registration cited alone | INDEPENDENT | INDEPENDENT | no |
| F3-perm | F-3 | 6 evaluation orders, members cited alone: distinct outcome vectors | (a=INDEPENDENT, b=INDEPENDENT, c=INDEPENDENT | (a=EXPLORATORY, b=EXPLORATORY, c=EXPLORATORY | yes |
| F4-flags dropped | F-4 | E0 on original (A), copy (flags dropped) relabelled (B): copy's run | INDEPENDENT | UNVERIFIED | yes |
| F4-flags rewritten | F-4 | E0 on original (A), copy (flags rewritten) relabelled (B): copy's run | INDEPENDENT | UNVERIFIED | yes |
| F4-trailing zeros padded | F-4 | E0 on original (A), copy (trailing zeros padded) relabelled (B): copy's run | INDEPENDENT | UNVERIFIED | yes |
| F4-flags dropped+padded+reordered | F-4 | E0 on original (A), copy (flags dropped+padded+reordered) relabelled (B): copy's run | INDEPENDENT | UNVERIFIED | yes |
| F4-subset with flags dropped | F-4 | E0 on original (A), copy (subset with flags dropped) relabelled (B): copy's run | INDEPENDENT | UNVERIFIED | yes |
| F4-ctrl-prices | CONTROL | relabelled instrument, same time grid, different prices | INDEPENDENT | INDEPENDENT | no |
| F4-ctrl-fresh | CONTROL | relabelled instrument, later disjoint period | INDEPENDENT | INDEPENDENT | no |
| F4-registry-order | F-4 | exact reordered relabelled copy: first run E1 under registry order AB / BA | INDEPENDENT / UNVERIFIED | UNVERIFIED / UNVERIFIED | yes |
| F1-default | F-1 | E1 INDEPENDENT, then a duplicate preregistration id is appended: E1 now | UNVERIFIED | UNVERIFIED | no |
| F1-before | CONTROL | same book before the invalid append | INDEPENDENT | INDEPENDENT | no |
| N1-36 | REGRESSION | three overlapping variants, 36 registration x evaluation orders: distinct outcome vectors | (a=EXPLORATORY, b=EXPLORATORY, c=EXPLORATORY | (a=EXPLORATORY, b=EXPLORATORY, c=EXPLORATORY | no |
