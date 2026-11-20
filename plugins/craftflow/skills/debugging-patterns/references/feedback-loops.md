# Feedback loops: build the red command first

Adapted from the diagnosing-bugs skill in [mattpocock/skills](https://github.com/mattpocock/skills) (MIT, see
`NOTICE`). With a tight pass/fail signal that goes red on this bug, bisection, hypotheses and instrumentation all just
consume it. Without one, reading more code rarely finds the cause. Spend disproportionate effort here.

## Ways to build one, roughly in this order

1. **Failing test** at whatever seam reaches the bug: unit, integration or end to end.
2. **HTTP script** (`curl` or a small client) against a running dev server.
3. **CLI invocation** with a fixture input, diffing stdout against a known-good snapshot.
4. **Headless browser** script (Playwright) that drives the UI and asserts on DOM, console or network.
5. **Replay a captured** request, payload or event log through the code path in isolation.
6. **Throwaway harness**: a minimal subset of the system (one service, faked dependencies) that hits the bug path
   with one call.
7. **Property** or fuzz loop: for "sometimes wrong", run many random inputs and look for the failure mode.
8. **Bisection** harness: when the bug appeared between two known states, script "check state X" so
   `git bisect run` can drive it.
9. **Differential** loop: the same input through the old and new version (or two configs), diffing outputs.
10. **Human in the loop** script, last: when a person must click, script their steps so the loop is still structured
    and their observations come back as output.

## Tighten it

- **Faster:** cache setup, skip unrelated init, narrow the test scope. Seconds, not minutes.
- **Sharper:** assert on the user's exact symptom, not "didn't crash".
- **Deterministic:** pin time, seed randomness, isolate the filesystem, fake the network.

For a non-deterministic bug the goal is a higher reproduction rate, not a clean repro: loop the trigger, parallelise,
add stress, narrow timing windows. A 50% repro is debuggable; 1% is not yet.

## Done when

You can name **one command** that you have already run, whose output you show (secrets redacted), and that is:

- red-capable: it drives the real bug path and asserts the user's exact symptom,
- deterministic: the same verdict every run, or a pinned high rate for a flaky bug,
- fast: seconds,
- agent-runnable: unattended, or through a human-in-the-loop script.

Then minimise: cut inputs, callers, config and steps one at a time, re-running after each cut, until every remaining
element is load-bearing. The minimised repro becomes the regression test; the original command proves the fix.

## When you cannot build one

Stop and say so. List what you tried. Ask for one of: access to an environment that reproduces it, a captured
artifact (log dump, HAR file, recording with timestamps), or permission to add temporary instrumentation. Do not move
on to hypotheses without a loop.
