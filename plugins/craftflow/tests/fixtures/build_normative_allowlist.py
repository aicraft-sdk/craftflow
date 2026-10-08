"""ONE-OFF, DO NOT RE-RUN: it already ran once to fold the doubt-verifier normative review into
router-normative-allowlist.json; re-running appends nothing new but rewrites the committed file. Kept as the
provenance of that file. The write is atomic (mkstemp + os.replace) in case it is ever run again.

Fold the doubt-verifier normative review into router-normative-allowlist.json.

Matches each review entry to a baseline unit by (file, line), falling back to a raw-prefix match,
and stores the unit's normalized text via the inventory's own `normalize` (via unit["norm"]).
Usage: python3 build_normative_allowlist.py [BASELINE_SHA]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent.parent
sys.path.insert(0, str(PLUGIN / "scripts"))
import craftflow_rule_inventory as inv  # noqa: E402

SHA = sys.argv[1] if len(sys.argv) > 1 else "0439c339"
REVIEW = json.loads((HERE / "router-normative-review.doubt-verifier.json").read_text(encoding="utf-8"))
ALLOW_PATH = HERE / "router-normative-allowlist.json"
allow = inv.cor.load_json(ALLOW_PATH)

rm = inv.load_readmap(str(HERE / "router-readmap.baseline.json"))
corpus = inv.cor.read_corpus(PLUGIN, SHA, rm)
units = {}
for f in {e["file"] for e in REVIEW["entries"]}:
    units[f] = inv.extract_units(corpus[f], f)

seen = {e["text"] for e in allow["entries"]}
fallback = 0
for e in REVIEW["entries"]:
    cand = [u for u in units[e["file"]] if u["line"] == e["line"]]
    key = " ".join(e["raw"].split())[:60]
    cand = [u for u in cand if " ".join(u["raw"].split()).startswith(key)] or cand
    if not cand:
        fallback += 1
        cand = [u for u in units[e["file"]] if " ".join(u["raw"].split()).startswith(key)]
    if len(cand) != 1:
        raise SystemExit(f"unmatched/ambiguous entry {e['file']}:{e['line']} ({len(cand)} candidates)")
    u = cand[0]
    if u["norm"] in seen:
        continue
    seen.add(u["norm"])
    allow["entries"].append({"text": u["norm"], "source": f"{e['file']}:{u['line']}@{SHA}", "reason": e["reason"]})

inv.cor.atomic_write(
    ALLOW_PATH,
    ('{\n  "version": %d,\n  "entries": [\n%s\n  ]\n}\n'
     % (allow["version"], ",\n".join("    " + json.dumps(x, ensure_ascii=False) for x in allow["entries"]))).encode("utf-8"))
print(json.dumps({"review_entries": len(REVIEW["entries"]), "distinct_texts": len(allow["entries"]), "fallback_matches": fallback}))
