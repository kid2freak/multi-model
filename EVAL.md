# Eval log

Re-run any row by replaying the commands in `~/.multi-model/runs/<path>/`.

## 2026-09-20 — initial validation (grok-4.6 effort medium, jev-1.13.0)

| # | Line | Case | Planted defect | Grok | TypeSafe filter | Gate | Result |
|---|------|------|----------------|------|-----------------|------|--------|
| 1 | leetcode | 704 binary search | `while lo<hi` + `hi=mid` (misses last element) | 3 findings, all real, concrete counterexamples | kept 3 (p .95/.95/.79); **dropped 1 planted false positive** ("int32 overflow in Python", p .13) | FAIL (tests_pass .01, code_matches .13) | ✅ blocked |
| 1b | leetcode | 704 fixed | none | — | — | PASS (all ≥ .98, clarity 1.0) | ✅ negative control |
| 2 | leetcode | 146 LRU cache | none | verdict sound, 0 findings, 9s | n/a | PASS (≥ .92) | ✅ no false block |
| 3 | leetcode | 53 max subarray | `best = 0` (fails all-negative) | 2 findings, both real | kept 2 (p .95/.95) | — | ✅ subtle bug caught |
| 4 | general | ext-count CLI | `--top 0` accepted (spec says exit 2) | 2 findings: planted one **+ unplanned real one** (symlinked files counted) | kept 2 (p .93/.87) | after fix: PASS (readiness .91) | ✅ found a bug the author missed |
| 5 | paper | synthetic draft | over-claiming, invalid inference, mis-attributed citation | 8 findings incl. 2 unplanned (17× vs log₂ dimension mismatch; missing 表 1), 117s | kept 8 (p .66–.96) | gate-paper FAIL: overclaiming .98, logic_gaps .98; claim check flagged exactly the 3 unsupported claims (p .02/.03/.02), passed the 2 supported ones (.92/.84), borderline .69 on a claim that omits a qualifier | ✅ |
| 6 | paper | researcher role | — | provenance of Bentley "90%" quote: primary CACM 1983 source + page, 6 documented distortions in popular retellings; 145s, **$0.43** | — | — | ✅ but expensive |

Observations
- Filter separates real from fake: real findings p ≥ .66, planted fake p = .13 (threshold .6).
- Grok input is always ~24k tokens (CLI overhead); reviews cost $0.02–0.03. Researcher with web is ~20× that.
- Both "unplanned" catches (#4 symlink, #5 dimension mismatch) came from Grok, i.e. from the model that did not write the artifact — the cross-model effect the design bets on.
- Not yet measured: Claude-only baseline on the same cases; long real PDFs through the critic; Windows/UE5 line (out of scope on this Mac).
