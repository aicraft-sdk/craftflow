# Edge-case pass

An agent doesn't get confused by a gap in a spec the way a person does: it fills the gap with plausible code and
ships it. So walk these eight categories, in this order, before the plan is final. The order is deliberate: each
category catches what reviewers tend to miss after the one before. After the
[edge case discovery pass](https://x.com/nurijanian/status/2074047145859228099) (one pass on a real connector spec
found 27 missing scenarios: refunds, idempotency, auth checkpoints, currency).

1. **User types**: new, returning, admin, guest, a user with no data, one with a very large amount of data.
2. **Contexts of use**: device, locale and time zone, offline or a slow network, a second tab open at the same time.
3. **Unexpected inputs and system failures**: empty, huge, malformed, Unicode and emoji, a dependency that times
   out or errors, partial writes.
4. **User error**: double submit, back button, cancel halfway, retry after a failure, a wrong value fixed later.
5. **Feature interactions**: what else reads or writes the same data, and what happens when both run.
6. **Load**: many items, many users at once, a burst after downtime, rate limits.
7. **Security and privacy**: who may see or change this, what lands in logs, what an attacker would send.
8. **Accessibility**: keyboard only, a screen reader, zoom, reduced motion, color contrast.

For each category, write one row per scenario the spec does not already answer:

| Category | Scenario | Defined behavior | Source |
|---|---|---|---|
| User error | Double submit of the payment form | One charge; the second request returns the first result | spec §3 / asked user / assumed |

- A scenario with no defined behavior is an open question for the user, not a guess. If the plan goes ahead
  anyway, mark the row `assumed` and say so in the plan's risks.
- Skip a category in one line when it cannot apply ("no UI: accessibility n/a"), so the reader sees it was checked.
- Scale to the work: a `critical_path` plan fills every category; a small plan lists only the categories that
  produced a row. Each row with defined behavior becomes an acceptance rule or a test-value gate case.
