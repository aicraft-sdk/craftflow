# Invocation prompts — Harness Engineering knowledge base

> Copy-paste snippets to pair with `harness-engineering-knowledge.md`. Attach (or paste) that file,
> then use one of these so you don't have to write the framing each time. Tool-agnostic.

## Quick primer (one line)
Use the attached `harness-engineering-knowledge.md` as your authoritative base knowledge on harness
engineering. Follow its two-mode usage block. Then handle my request below.

## Evaluate mode (review a harness / repo / AGENTS.md)
Using the attached `harness-engineering-knowledge.md` as ground truth, operate in **Evaluate mode**.
Apply the §7 rubric to what I paste below:
- Score each of the five subsystems 0–2 (max 10).
- Credit ONLY enforced mechanisms — a document that isn't enforced by a gate does not count.
- End with: (a) the weakest subsystem, (b) the single highest-leverage fix, (c) which §4 failure
  mode that fix prevents.
Be specific and quote the actual files. Do not be generous; flag doc-theater.

Harness to review:
[paste AGENTS.md / repo tree / setup here]

## Answer / design mode (ask a question or get a design)
Using the attached `harness-engineering-knowledge.md` as ground truth, operate in **Answer mode**.
Ground your answer in the five subsystems (§2) and the failure catalog (§4). Prefer concrete,
evidence-based guidance over generic best practices. If the real fix reduces to "write a better
prompt," say so explicitly — that is out of scope for this domain.

My question:
[your question here]

## Diagnose mode (something is going wrong with my agent)
Using the attached `harness-engineering-knowledge.md` as ground truth: I'm seeing the following
agent behavior — [describe symptom, e.g. "declares done but the feature is broken"]. Map it to the
most likely §4 failure mode(s), explain the root cause, and give me the specific harness change
(which subsystem, which artifact) that fixes it. Keep it actionable.
