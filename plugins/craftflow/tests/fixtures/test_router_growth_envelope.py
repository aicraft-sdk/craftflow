#!/usr/bin/env python3
"""ADR-0074 router growth envelope test law (plan H2 tasks 16-17, AD-3).

Run: python3 tests/fixtures/test_router_growth_envelope.py
The logic lives in router_envelope.py (shared with test_router_budget.py). The mutation controls run on literal fixtures; the live check
reads router-growth-envelope.json, router-budget.json and the measured tree, and stays red until David signs and the amendments are
recorded at the signature commit (C6/C7): decided_at_sha is "PENDING-SIGNATURE" before that, and the ceilings sit at the pre-envelope values.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[2]
REPO_TOP = PLUGIN_ROOT.parents[3]  # the git top level (REPO_ROOT is tools/, kept for the existing git -C calls)
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import router_envelope as renv  # noqa: E402
import test_router_budget as trb  # noqa: E402
import craftflow_router_readset as rs  # noqa: E402

ENVELOPE = trb.FIXTURES / "router-growth-envelope.json"
REL_ENVELOPE = "tools/craftflow-plugin/plugins/craftflow/tests/fixtures/router-growth-envelope.json"
_passes = 0
_errors: list = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason or 'assertion false'}")
        print(f"  FAIL: {name}: {reason or 'assertion false'}")


def is_ancestor_of_head(sha: str) -> bool:
    if not isinstance(sha, str) or not sha:
        return False
    if not renv.HEX_SHA.match(sha):  # never hand an option-shaped value to git
        return False
    return subprocess.run(["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", sha, "HEAD"], capture_output=True).returncode == 0


def commit_touches(root: Path, sha: str, rel: str) -> bool:
    """True when the commit `sha` changes the file `rel` (the signing commit must touch the ADR)."""
    if not isinstance(sha, str) or not renv.HEX_SHA.fullmatch(sha):
        return False
    # A merge is judged against its first parent (plain `git show` prints a combined diff that hides it); a root commit against nothing.
    has_parent = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q", f"{sha}^1"], capture_output=True).returncode == 0
    cmd = ["diff", "--name-only", f"{sha}^1", sha] if has_parent else ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha]
    res = subprocess.run(["git", "-C", str(root), *cmd], capture_output=True, text=True)
    return res.returncode == 0 and rel in res.stdout.split("\n")


_REF = re.compile(r"[0-9a-f]{7,40}(\^1)?")


def adr_blob(root: Path, ref: str) -> "bytes | None":
    """The committed ADR at `ref` (a hex sha, or `<sha>^1` for its first parent); None when unreadable (e.g. a root commit's parent)."""
    if not isinstance(ref, str) or not _REF.fullmatch(ref):
        return None
    res = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{renv.REL_ADR}"], capture_output=True)
    return res.stdout if res.returncode == 0 else None


def _text_or_none(raw: "bytes | None") -> "str | None":
    return None if raw is None else raw.decode("utf-8")


def file_at(root: Path, sha: str, rel: str) -> "bytes | None":
    """The committed bytes of `rel` at `sha`, None when the commit or path does not exist."""
    if not isinstance(sha, str) or not renv.HEX_SHA.match(sha):
        return None
    res = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{rel}"], capture_output=True)
    return res.stdout if res.returncode == 0 else None


# ---------------------------------------------------------------------------------------------- fixtures


SIGNED_ADR = "---\nadr: \"0074\"\nstatus: accepted\ndate: \"2026-10-28\"\n---\n\n## Signature\n\nDavid Gracia (2026-10-29)\n"
PROPOSED_ADR = SIGNED_ADR.replace("status: accepted", "status: proposed").replace("David Gracia (2026-10-29)", "Pending")
SIGNED_FIELDS = ("base", "allowances", "new_route_ceilings")
SIGN_SHA = "a" * 40


def signed_world() -> dict:
    """A good world after the signature: a hex sha, a signed ADR, and the envelope fields as committed at that sha."""
    keys = trb.KEYS
    w = {"env": {"adr": "ADR-0074", "decided_by": "David", "decided_at_sha": SIGN_SHA,
                    "base": {k: 1000 for k in keys}, "allowances": {k: 500 for k in keys},
                    "new_route_ceilings": {r: 2000 for r in renv.NEW_ROUTES}, "consumed": []},
            "ceilings": {k: 1200 for k in keys}, "measured": {k: 1100 for k in keys}, "caps": {k: 1600 for k in keys},
            "totals": {r: 1900 for r in renv.NEW_ROUTES}, "origin": None, "raises": []}
    w["adr_text"] = SIGNED_ADR
    w["touches_adr"] = lambda sha: sha == SIGN_SHA
    w["blobs"] = {SIGN_SHA: SIGNED_ADR, SIGN_SHA + "^1": PROPOSED_ADR}
    w["at_sha"] = {k: copy.deepcopy(w["env"][k]) for k in SIGNED_FIELDS}
    return w


world = signed_world


def problems_of(w: dict) -> list:
    sha = w["env"]["decided_at_sha"]
    return renv.problems(w["env"], w["origin"], w["ceilings"], w["measured"], w["caps"], w["totals"], w["raises"],
                         lambda s: s == SIGN_SHA, adr_text=w.get("adr_text"), touches_adr=w.get("touches_adr"),
                         adr_at=(lambda ref: w["blobs"].get(ref)) if "blobs" in w else None,
                         envelope_at=(lambda s: w["at_sha"] if s == sha else None) if "at_sha" in w else None)


def test_shape_and_good_world() -> None:
    w = world()
    check("good_fixture_has_no_problems", problems_of(w) == [], str(problems_of(w)))
    short = world()
    del short["env"]["consumed"]
    check("shape_missing_field_is_one_problem", len(problems_of(short)) == 1 and "keys differ" in problems_of(short)[0], str(problems_of(short)))
    badentry = world()
    badentry["env"]["consumed"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1, "to": 2}]
    check("shape_consumed_entry_needs_all_fields", len(problems_of(badentry)) == 1, str(problems_of(badentry)))


def test_mutation_controls_each_give_exactly_one_problem() -> None:
    over = world()
    over["ceilings"]["kernel_bytes"] = 1501  # base 1000 + allowance 500 = 1500
    over["caps"]["kernel_bytes"] = 2000
    notag = world()
    notag["raises"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1200, "at_sha": "x"}]
    edited = world()
    edited["origin"] = copy.deepcopy(edited["env"])
    edited["origin"]["consumed"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1200, "at_sha": "x"}]
    edited["env"]["consumed"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1300, "at_sha": "x"}]
    agent = world()
    agent["env"]["decided_by"] = "agent"
    detached = world()
    detached["env"]["decided_at_sha"] = "PENDING-SIGNATURE"
    capped = world()
    capped["caps"]["readset.BUILD"] = 1499  # base + allowance above the effective cap
    slow = world()
    slow["measured"]["protocol_bytes"] = 1201  # measured above the ceiling
    route = world()
    route["totals"]["QA"] = 2001
    cases = {"allowance_exceeded": over, "missing_tag": notag, "consumed_edited": edited, "decided_by_agent": agent,
             "sha_not_ancestor": detached, "allowance_above_cap": capped, "measured_above_ceiling": slow, "route_above_signed_ceiling": route}
    for name, w in cases.items():
        got = problems_of(w)
        check(f"mutation_{name}_one_problem", len(got) == 1, str(got))
    appended = world()
    appended["origin"] = copy.deepcopy(appended["env"])
    appended["env"]["consumed"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1200, "at_sha": "x"}]
    check("consumed_append_is_allowed", problems_of(appended) == [], str(problems_of(appended)))


def test_signed_envelope_is_content_bound() -> None:
    good = signed_world()
    check("signed_good_world_has_no_problems", problems_of(good) == [], str(problems_of(good)))

    def one(name: str, w: dict, needle: str) -> None:
        got = problems_of(w)
        check(f"signed_mutation_{name}_one_problem", len(got) == 1 and needle in got[0], str(got))

    tampered = signed_world()
    tampered["env"]["allowances"]["kernel_bytes"] += 100  # still within the cap, so only the content binding can catch it
    tampered["caps"]["kernel_bytes"] = 99999
    one("allowance_tampered_after_signing", tampered, "allowances")
    base = signed_world()
    base["env"]["base"]["protocol_bytes"] -= 1
    base["caps"]["protocol_bytes"] = 99999
    one("base_tampered_after_signing", base, "base")
    ceil = signed_world()
    ceil["env"]["new_route_ceilings"]["QA"] += 1
    one("route_ceiling_tampered_after_signing", ceil, "new_route_ceilings")
    notouch = signed_world()
    notouch["touches_adr"] = lambda sha: False
    one("sha_does_not_touch_the_adr", notouch, "does not touch")
    proposed = signed_world()
    proposed["adr_text"] = SIGNED_ADR.replace("status: accepted", "status: proposed")
    one("adr_still_proposed", proposed, "accepted")
    nosig = signed_world()
    nosig["adr_text"] = SIGNED_ADR.replace("David Gracia (2026-10-29)", "Pending: David signs in the H2 signing session.")
    one("signature_line_missing", nosig, "signature")
    other = signed_world()
    other["adr_text"] = SIGNED_ADR.replace("David Gracia (2026-10-29)", "Some Agent (2026-10-29)")
    one("signature_by_someone_else", other, "signature")
    commented = signed_world()
    commented["adr_text"] = SIGNED_ADR.replace("status: accepted", "status: accepted # not yet proposed")
    check("status_inline_comment_is_stripped", problems_of(commented) == [], str(problems_of(commented)))
    upper = signed_world()
    upper["adr_text"] = SIGNED_ADR.replace("status: accepted", "status: Accepted")
    one("status_is_case_sensitive", upper, "accepted")
    absent = signed_world()
    del absent["at_sha"]["base"]
    one("envelope_file_unreadable_at_the_sha", absent, "base")
    hexless = signed_world()
    hexless["env"]["decided_at_sha"] = "--oops"
    got = problems_of(hexless)
    check("signed_sha_must_be_hex", any("hex" in p for p in got), str(got))
    unverifiable = signed_world()
    unverifiable["touches_adr"] = None
    got = problems_of(unverifiable)
    check("signed_without_a_verifier_is_a_problem", any("cannot be verified" in p for p in got), str(got))
    consumed = signed_world()
    consumed["env"]["consumed"] = [{"phase": "H3", "key": "kernel_bytes", "from": 1000, "to": 1100, "at_sha": "x"}]
    check("consumed_growth_after_signing_is_not_content_drift", problems_of(consumed) == [], str(problems_of(consumed)))
    pending = world()
    pending["env"]["decided_at_sha"] = "PENDING-SIGNATURE"
    pending["adr_text"] = SIGNED_ADR.replace("status: accepted", "status: proposed")
    got = problems_of(pending)
    check("pending_signature_keeps_the_old_single_problem", len(got) == 1 and "ancestor" in got[0], str(got))


def test_signed_envelope_is_bound_to_the_blob_at_the_sha() -> None:
    """H-A: the ADR must be accepted+signed AT decided_at_sha, and that commit must be the one that introduced the signature."""
    def one(name: str, w: dict, needle: str) -> None:
        got = problems_of(w)
        check(f"blob_{name}_one_problem", len(got) == 1 and needle in got[0], str(got))

    draft = signed_world()
    draft["blobs"][SIGN_SHA] = PROPOSED_ADR  # the sha points at a commit whose ADR is still the draft
    got = problems_of(draft)
    check("blob_sha_with_draft_adr_fails", any("at decided_at_sha" in p for p in got) and len(got) >= 1, str(got))
    later = signed_world()
    later["blobs"][SIGN_SHA + "^1"] = SIGNED_ADR  # the signature was already there before this commit
    one("sha_is_not_the_introducing_commit", later, "introduced")
    root = signed_world()
    del root["blobs"][SIGN_SHA + "^1"]  # a root commit has no parent: nothing was signed before it
    check("blob_root_commit_signing_is_allowed", problems_of(root) == [], str(problems_of(root)))
    unreadable = signed_world()
    del unreadable["blobs"][SIGN_SHA]
    got = problems_of(unreadable)
    check("blob_unreadable_at_the_sha_fails", any("at decided_at_sha" in p for p in got), str(got))
    check("hex_sha_rejects_a_trailing_newline", renv.HEX_SHA.match("a" * 40 + "\n") is None and renv.HEX_SHA.match("a" * 40) is not None, "the pattern itself must reject a trailing newline")
    nl = signed_world()
    nl["env"]["decided_at_sha"] = SIGN_SHA + "\n"
    got = problems_of(nl)
    check("signed_sha_with_trailing_newline_is_not_hex", any("hex" in p for p in got), str(got))


def test_origin_envelope_unreadable_is_a_visible_skip() -> None:
    env, note = renv.origin_envelope(False, lambda ref, rel: ("ok", b"{}"))
    check("origin_unavailable_gives_a_skip_reason", env is None and note is not None and note.startswith("SKIP: ") and "origin/main" in note, repr((env, note)))
    env, note = renv.origin_envelope(False, lambda ref, rel: ("ok", b"{}"), ci=True)
    check("origin_unavailable_in_ci_is_a_failure", env is None and note is not None and note.startswith("FAIL: "), repr((env, note)))
    env, note = renv.origin_envelope(True, lambda ref, rel: ("absent", None))
    check("origin_without_the_file_is_first_introduction_not_a_skip", env is None and note is None, repr((env, note)))
    env, note = renv.origin_envelope(True, lambda ref, rel: ("error", None))
    check("origin_git_failure_is_a_skip_not_first_introduction", env is None and note is not None and note.startswith("SKIP: "), repr((env, note)))
    env, note = renv.origin_envelope(True, lambda ref, rel: ("error", None), ci=True)
    check("origin_git_failure_in_ci_is_a_failure", env is None and note is not None and note.startswith("FAIL: "), repr((env, note)))
    env, note = renv.origin_envelope(True, lambda ref, rel: ("ok", b'{"consumed": []}'))
    check("origin_with_the_file_is_parsed", env == {"consumed": []} and note is None, repr((env, note)))


def test_git_show_state_distinguishes_absent_from_failure() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        def git(*a: str) -> None:
            subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True)
        git("init", "-q")
        (Path(d) / "f.txt").write_text("hi\n")
        git("add", "f.txt")
        git("commit", "-q", "-m", "f")
        root = Path(d)
        check("show_state_ok_for_a_present_path", trb.git_show_state("HEAD", "f.txt", root) == ("ok", b"hi\n"), repr(trb.git_show_state("HEAD", "f.txt", root)))
        check("show_state_absent_for_a_missing_path", trb.git_show_state("HEAD", "nope.txt", root) == ("absent", None), repr(trb.git_show_state("HEAD", "nope.txt", root)))
        check("show_state_error_for_a_missing_ref", trb.git_show_state("origin/nowhere", "f.txt", root) == ("error", None), repr(trb.git_show_state("origin/nowhere", "f.txt", root)))
        check("show_state_error_when_git_cannot_run_there", trb.git_show_state("HEAD", "f.txt", root / "missing-dir") == ("error", None), "a non-repo directory is a git failure")


def test_git_helpers_against_a_scratch_repo() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        def git(*a: str) -> str:
            return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True).stdout.strip()
        git("init", "-q")
        (Path(d) / "adr.md").write_text("one\n")
        git("add", "adr.md")
        git("commit", "-q", "-m", "adr")
        adr_sha = git("rev-parse", "HEAD")
        (Path(d) / "other.txt").write_text("x\n")
        git("add", "other.txt")
        git("commit", "-q", "-m", "other")
        other_sha = git("rev-parse", "HEAD")
        check("commit_touches_true_for_the_signing_commit", commit_touches(Path(d), adr_sha, "adr.md"), "adr commit should touch adr.md")
        check("commit_touches_false_for_another_commit", not commit_touches(Path(d), other_sha, "adr.md"), "other commit should not touch adr.md")
        check("commit_touches_false_for_an_option_shaped_sha", not commit_touches(Path(d), "--oops", "adr.md"), "an option-shaped sha must not run")
        (Path(d) / "adr.md").write_text("two\n")
        git("add", "adr.md")
        git("commit", "-q", "-m", "adr2")
        git("checkout", "-q", "-b", "side", adr_sha)
        (Path(d) / "side.txt").write_text("s\n")
        git("add", "side.txt")
        git("commit", "-q", "-m", "side")
        git("checkout", "-q", "-")
        git("merge", "-q", "--no-ff", "-m", "merge", "side")
        merge_sha = git("rev-parse", "HEAD")
        (Path(d) / "adr.md").write_text("three\n")
        git("add", "adr.md")
        git("commit", "-q", "-m", "adr3")
        git("checkout", "-q", "-b", "side2", "HEAD~1")
        (Path(d) / "side2.txt").write_text("s\n")
        git("add", "side2.txt")
        git("commit", "-q", "-m", "side2")
        git("checkout", "-q", "-")
        git("merge", "-q", "--no-ff", "-m", "merge2", "side2")
        merge2_sha = git("rev-parse", "HEAD")
        git("checkout", "-q", "-b", "side3", "HEAD")
        (Path(d) / "adr.md").write_text("four\n")
        git("add", "adr.md")
        git("commit", "-q", "-m", "adr4 on side")
        git("checkout", "-q", "-")
        git("merge", "-q", "--no-ff", "-m", "merge3", "side3")
        merge3_sha = git("rev-parse", "HEAD")
        check("commit_touches_true_for_a_merge_that_lands_a_change_to_the_file", commit_touches(Path(d), merge3_sha, "adr.md"), "a merge bringing in an adr.md change touches it against the first parent")
        check("commit_touches_false_for_a_merge_that_brings_in_other_files", not commit_touches(Path(d), merge_sha, "adr.md"), "a merge of unrelated work must not count as touching adr.md")
        check("commit_touches_is_first_parent_for_a_merge", not commit_touches(Path(d), merge2_sha, "adr.md") and commit_touches(Path(d), merge2_sha, "side2.txt"), "merge diff must be against the first parent")
        check("file_at_reads_the_committed_blob", file_at(Path(d), adr_sha, "adr.md") == b"one\n", repr(file_at(Path(d), adr_sha, "adr.md")))
        check("file_at_is_none_for_a_missing_path", file_at(Path(d), adr_sha, "nope.md") is None, "missing path should be None")


def test_signature_simulation_in_a_scratch_repo() -> None:
    """The real ADR and envelope files, signed through real commits in a scratch repo: properly signed passes, each tamper fails."""
    import tempfile
    real_adr = (REPO_TOP / renv.REL_ADR).read_text(encoding="utf-8")
    real_env = json.loads(ENVELOPE.read_text(encoding="utf-8"))
    pending_note = "Pending: David signs in the H2 signing session. This ADR stays `status: proposed` until then."
    signed_adr = real_adr.replace("status: proposed", "status: accepted", 1).replace(pending_note, "David Gracia (2026-10-29)", 1)
    check("simulation_inputs_are_the_pending_originals", signed_adr != real_adr and "David Gracia (2026-10-29)" in signed_adr and pending_note in real_adr,
          "the live ADR no longer has the pending signature text this simulation replaces")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)

        def git(*a: str) -> str:
            return subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True, check=True).stdout.strip()

        def put(rel: str, text: str) -> None:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")

        def verdict(env: dict, adr_text: str) -> list:
            return renv.signed_problems(env, adr_text, lambda sha: commit_touches(root, sha, renv.REL_ADR),
                                        lambda sha: _json_or_none(file_at(root, sha, renv.REL_ENVELOPE)),
                                        adr_at=lambda ref: _text_or_none(adr_blob(root, ref)))

        git("init", "-q")
        put(renv.REL_ADR, real_adr)
        put(renv.REL_ENVELOPE, json.dumps(real_env, indent=2))
        git("add", ".")
        git("commit", "-q", "-m", "draft")
        draft_sha = git("rev-parse", "HEAD")
        put(renv.REL_ADR, signed_adr)
        git("add", ".")
        git("commit", "-q", "-m", "sign")
        sign_sha = git("rev-parse", "HEAD")
        put("unrelated.txt", "x\n")
        git("add", ".")
        git("commit", "-q", "-m", "unrelated")
        unrelated_sha = git("rev-parse", "HEAD")
        signed_env = dict(real_env, decided_at_sha=sign_sha)
        check("simulation_properly_signed_passes", verdict(signed_env, signed_adr) == [], str(verdict(signed_env, signed_adr)))
        grown = dict(signed_env, consumed=[{"phase": "H3", "key": "kernel_bytes", "from": 1, "to": 2, "at_sha": sign_sha}])
        check("simulation_consumed_growth_after_signing_passes", verdict(grown, signed_adr) == [], str(verdict(grown, signed_adr)))
        tampered = copy.deepcopy(signed_env)
        tampered["allowances"]["kernel_bytes"] += 1
        got = verdict(tampered, signed_adr)
        check("simulation_tampered_allowance_fails", len(got) == 1 and "allowances" in got[0], str(got))
        got = verdict(dict(signed_env, decided_at_sha=unrelated_sha), signed_adr)
        check("simulation_sha_not_touching_the_adr_fails", len(got) == 2 and any("does not touch" in p for p in got) and any("introduced" in p for p in got), str(got))
        got = verdict(signed_env, real_adr)
        check("simulation_adr_still_proposed_fails", len(got) == 2 and any("accepted" in p for p in got) and any("signature" in p for p in got), str(got))
        got = verdict(signed_env, signed_adr.replace("David Gracia (2026-10-29)", "(unsigned)"))
        check("simulation_missing_signature_line_fails", len(got) == 1 and "signature" in got[0], str(got))
        check("simulation_draft_commit_exists", bool(draft_sha) and draft_sha != sign_sha, "scratch history is wrong")
        got = verdict(dict(signed_env, decided_at_sha=draft_sha), signed_adr)
        check("simulation_sha_at_the_draft_commit_fails", any("at decided_at_sha" in p for p in got), str(got))
        bumped = copy.deepcopy(real_env)
        bumped["allowances"]["kernel_bytes"] += 100
        put(renv.REL_ADR, signed_adr + "\nA later amendment.\n")
        put(renv.REL_ENVELOPE, json.dumps(bumped, indent=2))
        git("add", ".")
        git("commit", "-q", "-m", "later amendment that touches the adr and bumps an allowance")
        later_sha = git("rev-parse", "HEAD")
        later_env = dict(bumped, decided_at_sha=later_sha)
        got = verdict(later_env, signed_adr)
        check("simulation_later_adr_commit_with_changed_allowance_fails", len(got) == 1 and "introduced" in got[0], str(got))
        check("simulation_real_signing_commit_still_passes_after_history_grows", verdict(signed_env, signed_adr) == [], str(verdict(signed_env, signed_adr)))


def test_check_raise_bounds_envelope_branch() -> None:
    def run(entries, to=1300, extra_keys=()):
        budget, origin, table = trb.raise_world()
        w = world()
        budget["ceilings"]["readset"]["BUILD"] = to
        budget["ceiling_raises"] = entries
        envelope = renv.checker(w["env"], {k: 1100 for k in trb.KEYS})
        return trb.check_raise_bounds(budget, origin, trb.fixture_projector(table), envelope=envelope)

    good = {"phase": "H2", "key": "readset.BUILD", "from": 1000, "to": 1300, "envelope": "ADR-0074", "at_sha": "abc"}
    check("envelope_raise_within_allowance_passes", run([good]) == [], str(run([good])))
    check("envelope_raise_above_allowance_one_problem", len(run([dict(good, to=1501)], to=1501)) == 1, str(run([dict(good, to=1501)], to=1501)))
    check("envelope_raise_with_p13_phase_one_problem", len(run([dict(good, phase="P13")])) == 1, str(run([dict(good, phase="P13")])))
    check("envelope_raise_from_not_origin_one_problem", len(run([dict(good, **{"from": 900})])) == 1, str(run([dict(good, **{"from": 900})])))
    mixed = [good, {"phase": "P4", "key": "readset.BUILD", "from": 1300, "to": 1300, "projected_delta": 300}]
    check("envelope_mixed_with_projection_raise_one_problem", len(run(mixed)) == 1, str(run(mixed)))
    check("raise_without_entry_still_fails_as_before", any("no ceiling_raises entry" in p for p in run([])), str(run([])))


def test_slack_and_append_only() -> None:
    w = world()
    budget = {"ceilings": {"kernel_bytes": 1300, "readset": {"BUILD": 1000}}, "ceiling_raises": [
        {"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1300, "envelope": "ADR-0074", "at_sha": "abc"}]}
    w["env"]["base"] = {"kernel_bytes": 1000, "readset.BUILD": 1000}
    check("slack_only_for_keys_with_an_envelope_raise", renv.slack(budget, w["env"]) == {"kernel_bytes": 300}, str(renv.slack(budget, w["env"])))
    origin = {"merged_phases": ["P13"], "ceiling_raises": [], "projection_revisions": [], "ceilings": {"kernel_bytes": 1000}, "measured_at_sha": "a",
              "enforced_caps_decision": {"amendments": []}}
    head = copy.deepcopy(origin)
    head["ceilings"]["kernel_bytes"] = 1300
    head["measured_at_sha"] = "b"
    head["ceiling_raises"] = [budget["ceiling_raises"][0]]
    head["enforced_caps_decision"]["amendments"] = [{"key": "kernel_bytes"}]
    check("envelope_only_change_accepts_the_allowed_fields", renv.envelope_only_change(origin, head) and trb.append_only_problems(origin, head, False) == [],
          str(trb.append_only_problems(origin, head, False)))
    other = copy.deepcopy(head)
    other["conditions"] = {"difference_17": False}
    origin["conditions"] = {"difference_17": True}
    check("envelope_only_change_rejects_other_fields", not renv.envelope_only_change(origin, other), "a conditions change passed as envelope-only")
    check("identical_budget_is_not_an_envelope_change", not renv.envelope_only_change(origin, copy.deepcopy(origin)), "no change counted as one")


# ---------------------------------------------------------------------------------------------- live check


def _json_or_none(raw: "bytes | None") -> "dict | None":
    return None if raw is None else json.loads(raw.decode("utf-8"))


def test_live_envelope() -> None:
    e = json.loads(ENVELOPE.read_text(encoding="utf-8"))
    budget = trb.load_budget()
    caps = trb.effective_caps(budget) if budget.get("enforced_caps") else None
    cap_of = {k: trb.get_key(caps, k) for k in trb.KEYS} if caps else {k: 0 for k in trb.KEYS}
    rm = rs.load_map(PLUGIN_ROOT / "config" / "router-readmap.json")
    totals = {r: rs.compute(r, PLUGIN_ROOT, readmap=rm, budget_path=Path("/nonexistent"))["start_total"] for r in renv.NEW_ROUTES}
    ceilings = {k: trb.ceiling(budget, k) for k in trb.KEYS}
    status, origin_budget = trb.origin_budget()
    origin_env, note = renv.origin_envelope(trb.origin_available(), trb.git_show_state, ci=bool(os.environ.get("CI")))
    if note:
        print(f"  {note}")
    if note and note.startswith("FAIL: "):
        check("live_origin_readable_in_ci", False, note)
    added = budget["ceiling_raises"][len((origin_budget or {}).get("ceiling_raises", [])):]
    adr_file = REPO_TOP / renv.REL_ADR
    adr_text = adr_file.read_text(encoding="utf-8") if adr_file.is_file() else ""
    got = renv.problems(e, origin_env, ceilings, trb.MEASURED, cap_of, totals, added, is_ancestor_of_head, adr_text=adr_text,
                        touches_adr=lambda sha: commit_touches(REPO_TOP, sha, renv.REL_ADR),
                        adr_at=lambda ref: _text_or_none(adr_blob(REPO_TOP, ref)),
                        envelope_at=lambda sha: _json_or_none(file_at(REPO_TOP, sha, renv.REL_ENVELOPE)))
    check("live_envelope_signed_and_within_bounds", not got, "; ".join(got) + " (expected red until the H2 signature commits C6/C7)")


def main() -> int:
    for name in sorted(n for n in globals() if n.startswith("test_")):
        try:
            globals()[name]()
        except Exception as exc:  # noqa: BLE001 - a crashing test is a failure of that test, reported by name
            check(name, False, f"{type(exc).__name__}: {exc}")
    print()
    for err in _errors:
        print(err)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
