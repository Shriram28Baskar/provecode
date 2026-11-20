"""Unit tests for label_tool.py and label_common.py.

Standard library unittest only. Every test uses a temporary folder with
synthetic sample lists, stand-in qualifying files and hand-made packet files,
an injected fake clock and scripted input. No real sample list, packet or
label file is read or written.

Run: python -I -B spike/labeling/test_label_tool.py
"""

import ast
import datetime
import hashlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
TOOL = HERE / "label_tool.py"
COMMON = HERE / "label_common.py"
THIS_FILE = Path(__file__).resolve()

_spec = importlib.util.spec_from_file_location("label_tool", TOOL)
label_tool = importlib.util.module_from_spec(_spec)
sys.modules["label_tool"] = label_tool
_spec.loader.exec_module(label_tool)
lt = label_tool
lc = lt.lc
rp = lt.rp

# Special values built without backslash escapes.
TAB = chr(9)
CR_BYTE = bytes([13])
LF_BYTE = bytes([10])
BOM_BYTES = bytes.fromhex("efbbbf")
REPLACEMENT_BYTES = bytes.fromhex("efbfbd")
FORBIDDEN_WORD = "res" + "erve"
FORBIDDEN_NAME = "pool_" + "snap" + "shot"

REPO_A = "acme/widgets"
REPO_B = "beta/gadgets"
FILES = (
    ("M", "tests/test_mod.py", "tests/test_mod.py", "-"),
    ("D", "tests/test_gone.py", "-", "-"),
    ("R", "tests/test_old.py", "tests/test_new.py", "87"),
)
BODY_MARKER = "PACKET-BODY-MARKER-NOT-TO-BE-PRINTED"
START_UTC = datetime.datetime(2026, 11, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)

N_UNIT = ["n", "n", "3", "3", ""]  # judgement, value_to_trivial, before, after, note


def w_unit(triggers=("n", "n", "n", "n"), subtype="1", note=""):
    # judgement, 4 triggers, subtype, justified, value_to_trivial, before, after, note
    return ["w", *triggers, subtype, "y", "n", "4", "2", note]


def fake_sha(n):
    return hashlib.sha1(f"commit-{n}".encode("ascii")).hexdigest()


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def packet_text(repo, sha, stratum, files=FILES, count=None):
    lines = [
        "PACKET",
        f"repo: {repo}",
        f"sha: {sha}",
        f"stratum: {stratum}",
        f"parent: {'0' * 40}",
        f"qualifying files: {len(files) if count is None else count}",
        "diff lines: 1",
        "CR characters normalised in test diff: 0",
        lc.FILES_HEADER,
        *[TAB.join(f) for f in files],
        lc.UNITS_NOT_LISTED,
        rp.SEPARATOR,
        "B. COMMIT MESSAGE",
        BODY_MARKER,
        rp.SEPARATOR,
        "C. OTHER CHANGED FILES",
        "(none)",
        rp.SEPARATOR,
        "D. TEST DIFF",
        "-    assert x == 1",
        rp.SEPARATOR,
        "END OF PACKET",
    ]
    return "\n".join(lines) + "\n"


PACKET_FILES = lc.parse_packet_header(packet_text(REPO_A, fake_sha(1), "random")).files


class FakeClock:
    def __init__(self, start=START_UTC):
        self.start = start
        self.seconds = 0.0

    def utc_now(self):
        return self.start + datetime.timedelta(seconds=self.seconds)

    def monotonic(self):
        return 500.0 + self.seconds

    def advance(self, seconds):
        self.seconds += seconds


class Crash(Exception):
    """Simulates a hard crash: neither the session nor main catches it."""


class Script:
    """Scripted input. A str item is the answer to the next prompt; an int or
    float item advances the fake clock before that answer; an exception class
    or instance is raised. When the items run out, EOFError is raised."""

    def __init__(self, clock, items):
        self.clock = clock
        self.items = list(items)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        while self.items:
            item = self.items.pop(0)
            if isinstance(item, str):
                return item
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                self.clock.advance(item)
                continue
            raise item
        raise EOFError


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.spike = self.root / "spike"
        self.spike.mkdir(parents=True)
        self.random_entries = [(REPO_A, fake_sha(1)), (REPO_B, fake_sha(2)), (REPO_A, fake_sha(3))]
        self.keyword_entries = [(REPO_B, fake_sha(4)), (REPO_A, fake_sha(5))]
        self.unlisted = fake_sha(99)
        self.sha = fake_sha(1)
        self.clock = FakeClock()
        self.config = self.make_config()
        for repo, sha in self.random_entries:
            self.write_packet(repo, sha, "random")
        for repo, sha in self.keyword_entries:
            self.write_packet(repo, sha, "keyword")

    # ------------------------------------------------------------ helpers

    def make_config(self):
        random_path = self.spike / "random_list.txt"
        keyword_path = self.spike / "keyword_list.txt"
        random_path.write_bytes("".join(f"{r} {s}\n" for r, s in self.random_entries).encode("ascii"))
        keyword_path.write_bytes("".join(f"{r} {s}\n" for r, s in self.keyword_entries).encode("ascii"))
        qualifying = {}
        for repo in (REPO_A, REPO_B):
            path = self.spike / f"stand_in_{repo.replace('/', '__')}.json"
            path.write_bytes(b'{"stand_in": true}\n')
            qualifying[repo] = rp.InputFile(path, sha256_of(path))
        rpc = rp.Config(
            repo_root=self.root,
            random_list=rp.InputFile(random_path, sha256_of(random_path)),
            keyword_list=rp.InputFile(keyword_path, sha256_of(keyword_path)),
            qualifying=qualifying,
        )
        return lt.Config(rpc, self.root, self.spike / "labels", self.spike / "packets")

    @property
    def labels(self):
        return self.config.labels_dir

    def write_packet(self, repo, sha, stratum, files=FILES, folder=None):
        folder = folder if folder is not None else self.config.packets_dir / repo.replace("/", "__")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{sha}.txt"
        path.write_bytes(packet_text(repo, sha, stratum, files).encode("utf-8"))
        return path

    def run_tool(self, argv, items=()):
        script = Script(self.clock, items)
        out, err = io.StringIO(), io.StringIO()
        console = lt.Console(input_fn=script, out=out, err=err)
        code = lt.main(list(argv), config=self.config, console=console, clock=self.clock)
        text = out.getvalue() + err.getvalue() + "".join(script.prompts)
        for form in {str(self.base), str(self.base.resolve()), self.base.as_posix(), self.base.resolve().as_posix()}:
            self.assertNotIn(form, text, "an absolute path was printed")
        self.assertNotIn(BODY_MARKER, text, "packet body was printed")
        self.script = script
        return code, out.getvalue(), err.getvalue()

    def start(self, items, sha=None):
        return self.run_tool(["start", sha or self.sha], items)

    def finish(self, sha=None, browsed="n", helpers="", notes=""):
        return [browsed, helpers, notes, (sha or self.sha)[:8]]

    def simple_session(self, sha=None):
        return ["1", "TestA.test_one", "listed", *N_UNIT, "done", *self.finish(sha)]

    def units(self):
        return lc.read_csv(self.labels / lc.UNITS_CSV, lc.UNITS_HEADER)

    def commits(self):
        return lc.read_csv(self.labels / lc.COMMITS_CSV, lc.COMMITS_HEADER)

    def events(self, sha=None):
        rows = lc.read_csv(self.labels / lc.SESSION_CSV, lc.SESSION_HEADER)
        return [r for r in rows if sha is None or r["sha"] == sha]

    def snapshot(self):
        if not self.labels.exists():
            return {}
        return {p.relative_to(self.labels).as_posix(): p.read_bytes() for p in self.labels.rglob("*") if p.is_file()}

    def mark_finished(self, repo, sha, stratum, minutes="1.0"):
        commit = lc.commit_row(
            sha=sha, stratum=stratum, repo=repo, first_pass_date="2026-11-20", minutes=minutes,
            browsed="N", helpers_consulted="", unit_count="0", notes="",
        )
        lc.finish_commit(self.labels, PACKET_FILES, [], commit, lc.session_event(sha, "finish", START_UTC, 0.0))


# ---------------------------------------------------------------- labeling flow


class LabelingTests(ToolTestCase):
    def test_derive_weakened_label(self):
        self.assertEqual(lc.derive_weakened_label([False] * 4), "WP")
        for i in range(4):
            triggers = [False] * 4
            triggers[i] = True
            self.assertEqual(lc.derive_weakened_label(triggers), "WC", i)
        with self.assertRaises(lc.LabelError):
            lc.derive_weakened_label([False] * 3)

    def test_session_wp_vs_wc_from_triggers(self):
        items = ["names", *[f"1 T.u{i}" for i in range(5)], "", "listed"]
        items += w_unit(("n", "n", "n", "n"))
        for i in range(4):
            triggers = ["n"] * 4
            triggers[i] = "y"
            items += w_unit(tuple(triggers))
        items += ["done", *self.finish()]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        rows = self.units()
        self.assertEqual([r["label"] for r in rows], ["WP", "WC", "WC", "WC", "WC"])
        self.assertEqual(
            [r["notes"] for r in rows],
            ["triggers=none", "triggers=test_deleted", "triggers=skip_xfail_added",
             "triggers=assert_removed", "triggers=assert_count_drop"],
        )
        self.assertTrue(all(r["subtype"] == "comparison loosened" for r in rows))
        self.assertTrue(all((r["justified"], r["value_to_trivial"]) == ("Y", "N") for r in rows))
        for question in lc.TRIGGER_QUESTIONS:
            self.assertIn(f"{question} (y/n): ", self.script.prompts)

    def test_trigger_note_with_free_text(self):
        items = ["1", "T.a", "listed", *w_unit(("n", "y", "n", "y"), note="moved to helper"), "done", *self.finish()]
        self.assertEqual(self.start(items)[0], 0)
        self.assertEqual(self.units()[0]["notes"], "triggers=skip_xfail_added,assert_count_drop; moved to helper")

    def test_n_and_u_labels(self):
        items = ["names", "1 T.n", "1 T.c", "1 T.r", "", "listed", "n", "y", "5", "5", "fine",
                 "c", "2", "2", "direction unclear", "r", "1", "0", "", "helper unknown", "done", *self.finish()]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        n, c, r = self.units()
        self.assertEqual((n["label"], n["value_to_trivial"], n["subtype"], n["justified"], n["notes"]), ("N", "Y", "", "", "fine"))
        self.assertEqual((c["label"], c["value_to_trivial"], c["notes"]), ("U/unclear", "", "direction unclear"))
        self.assertEqual((r["label"], r["notes"], r["before_lines"], r["after_lines"]), ("U/unresolvable", "helper unknown", "1", "0"))
        self.assertIn("a note is required.", out)

    def test_paths_by_file_status_and_unit_list(self):
        items = ["names", "1 T.m", "2 T.d", "3 T.r", "", "listed", *N_UNIT, *N_UNIT, *N_UNIT, "done", *self.finish()]
        self.assertEqual(self.start(items)[0], 0)
        rows = self.units()
        self.assertEqual([(r["base_path"], r["head_path"]) for r in rows], [
            ("tests/test_mod.py", "tests/test_mod.py"), ("tests/test_gone.py", ""), ("tests/test_old.py", "tests/test_new.py"),
        ])
        listing = (self.labels / lc.UNIT_LISTS_DIR / f"{self.sha}.txt").read_bytes().decode("utf-8")
        self.assertEqual(listing, "".join([
            TAB.join(["tests/test_mod.py", "tests/test_mod.py", "T.m"]) + "\n",
            TAB.join(["tests/test_gone.py", "-", "T.d"]) + "\n",
            TAB.join(["tests/test_old.py", "tests/test_new.py", "T.r"]) + "\n",
        ]))

    def test_exact_headers(self):
        self.assertEqual(",".join(lc.UNITS_HEADER),
                         "sha,stratum,repo,base_path,head_path,qualified_name,label,subtype,justified,"
                         "value_to_trivial,before_lines,after_lines,bulk,notes")
        self.assertEqual(",".join(lc.COMMITS_HEADER),
                         "sha,stratum,repo,first_pass_date,minutes,browsed,helpers_consulted,unit_count,notes")
        self.assertEqual(",".join(lc.SESSION_HEADER), "sha,event,utc_iso,elapsed_seconds")
        self.assertEqual(self.start(self.simple_session())[0], 0)
        for name, header in ((lc.UNITS_CSV, lc.UNITS_HEADER), (lc.COMMITS_CSV, lc.COMMITS_HEADER), (lc.SESSION_CSV, lc.SESSION_HEADER)):
            first = (self.labels / name).read_bytes().split(LF_BYTE, 1)[0]
            self.assertEqual(first, ",".join(header).encode("ascii"), name)

    def test_frozen_constants(self):
        self.assertEqual(lc.LABELS, ("WP", "WC", "N", "U/timebox", "U/unclear", "U/unresolvable"))
        self.assertEqual(lc.SUBTYPES, (
            "comparison loosened", "trivial bound", "exception widened", "match removed",
            "tolerance loosened", "cases removed", "assert removed", "skip/xfail added",
            "guard/swallow added", "test deleted", "other",
        ))
        self.assertEqual((lc.TIMEBOX_MINUTES, lc.BULK_MIN_UNITS), (15, 21))

    def test_written_files_lf_only_no_bom_no_cr(self):
        items = ["1", "T.a", "listed", "c", "1", "1", 'quote " and, comma', "done", *self.finish(notes="x, y")]
        self.assertEqual(self.start(items)[0], 0)
        files = [p for p in self.labels.rglob("*") if p.is_file()]
        self.assertEqual(len(files), 4)
        for path in files:
            data = path.read_bytes()
            with self.subTest(file=path.name):
                self.assertNotIn(CR_BYTE, data)
                self.assertFalse(data.startswith(BOM_BYTES))
                self.assertNotIn(REPLACEMENT_BYTES, data)
                self.assertTrue(data.endswith(LF_BYTE))
                data.decode("utf-8")
        self.assertEqual(self.units()[0]["notes"], 'quote " and, comma')

    def test_first_pass_date_and_minutes_taken_at_final_write(self):
        self.clock = FakeClock(datetime.datetime(2031, 2, 3, 23, 59, 0, tzinfo=datetime.timezone.utc))
        items = ["1", "T.a", "listed", *N_UNIT, "done", "n", "", "", 90, self.sha[:8]]
        self.assertEqual(self.start(items)[0], 0)
        (commit,) = self.commits()
        self.assertEqual(commit["first_pass_date"], "2031-02-04")
        self.assertEqual(commit["minutes"], "1.5")
        self.assertEqual((commit["unit_count"], commit["browsed"], commit["notes"]), ("1", "N", ""))
        self.assertEqual(self.events(self.sha)[-1]["event"], "finish")

    def test_helpers_validated_and_canonical(self):
        items = ["1", "T.a", "listed", *N_UNIT, "done", "y", "/abs/x.py:3", "src/a.py:0", " src/a.py:12 ; lib/b.py:3 ", "", self.sha[:8]]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.commits()[0]["helpers_consulted"], "src/a.py:12;lib/b.py:3")
        self.assertEqual(self.commits()[0]["browsed"], "Y")
        self.assertEqual(out.count("not accepted"), 2)

    def test_confirmation_requires_sha_prefix(self):
        items = ["1", "T.a", "listed", *N_UNIT, "done", "n", "", "", "yes", self.sha[:7], self.sha[:8]]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("not confirmed"), 2)

    def test_duplicate_unit_refused_at_entry(self):
        items = ["1", "T.a", "1", "T.a", "1", "T a", "listed", *N_UNIT, "done", *self.finish()]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        self.assertIn("already entered", out)
        self.assertEqual(len(self.units()), 1)


# ---------------------------------------------------------------- listing rules


class ListingTests(ToolTestCase):
    def test_labeling_refused_before_listed(self):
        items = ["1", "T.a", "n", "w", "b", "listed", *N_UNIT, "done", *self.finish()]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("Labeling is refused until you type listed."), 3)
        self.assertEqual(self.units()[0]["label"], "N")

    def test_done_before_listed_with_units(self):
        items = ["1", "T.a", "done", *N_UNIT, "done", *self.finish()]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        self.assertIn("done refused: 1 units are not labeled yet. The list is now marked listed", out)
        self.assertEqual(self.commits()[0]["notes"], "")

    def test_done_before_listed_without_units(self):
        code, out, err = self.start(["done", *self.finish()])
        self.assertEqual(code, 0, err)
        (commit,) = self.commits()
        self.assertEqual((commit["unit_count"], commit["notes"]), ("0", ""))
        self.assertEqual((self.labels / lc.UNIT_LISTS_DIR / f"{self.sha}.txt").read_bytes(), b"")

    def test_unit_added_after_listing(self):
        items = ["1", "T.a", "listed", *N_UNIT, "add", "2", "T.b", "n", "n", "4", "0", "",
                 "names", "3 T.c", "", *N_UNIT, "done", *self.finish(notes="free")]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        rows = self.units()
        self.assertEqual([r["qualified_name"] for r in rows], ["T.a", "T.b", "T.c"])
        self.assertEqual((rows[1]["label"], rows[1]["head_path"], rows[1]["bulk"]), ("N", "", "N"))
        self.assertEqual(self.commits()[0]["notes"], f"{lc.NOTE_ADDED_AFTER_LISTING}; free")

    def test_timebox_before_listed(self):
        code, out, err = self.start(["1", 901, "T.a", *self.finish()])
        self.assertEqual(code, 0, err)
        self.assertIn("TIMEBOX REACHED", out)
        (row,) = self.units()
        self.assertEqual((row["label"], row["notes"]), ("U/timebox", lc.NOTE_AUTO_TIMEBOX))
        self.assertEqual(self.commits()[0]["notes"], lc.NOTE_DURING_LISTING)

    def test_timebox_after_listed(self):
        items = ["names", "1 T.a", "1 T.b", "", "listed", "n", "n", "3", "3", 901, "", *self.finish()]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        a, b = self.units()
        self.assertEqual(a["label"], "N")
        self.assertEqual(b["label"], "U/timebox")
        self.assertNotIn(lc.NOTE_DURING_LISTING, self.commits()[0]["notes"])


# ---------------------------------------------------------------- timebox


class TimeboxTests(ToolTestCase):
    def test_expiry_in_the_middle_of_a_unit(self):
        items = ["names", "1 T.a", "1 T.b", "1 T.c", "", "listed", "n", 901, "y", "3", "2", "kept", *self.finish()]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        a, b, c = self.units()
        self.assertEqual((a["label"], a["value_to_trivial"], a["before_lines"], a["after_lines"], a["notes"]), ("N", "Y", "3", "2", "kept"))
        for row in (b, c):
            self.assertEqual(
                (row["label"], row["subtype"], row["justified"], row["value_to_trivial"], row["before_lines"], row["after_lines"], row["bulk"], row["notes"]),
                ("U/timebox", "", "", "", "", "", "N", lc.NOTE_AUTO_TIMEBOX),
            )
        self.assertLess(out.index("unit 1 of 3"), out.index("TIMEBOX REACHED"))
        self.assertIn("timebox", [e["event"] for e in self.events()])
        self.assertEqual(self.commits()[0]["notes"], "")
        self.assertEqual(self.commits()[0]["minutes"], "15.0")

    def test_expiry_during_paste_block_reads_to_blank_line(self):
        code, out, err = self.start(["names", "1 T.a", 901, "1 T.b", "", *self.finish(browsed="y")])
        self.assertEqual(code, 0, err)
        self.assertEqual([r["label"] for r in self.units()], ["U/timebox", "U/timebox"])
        self.assertEqual(self.commits()[0]["browsed"], "Y")
        self.assertEqual(self.commits()[0]["notes"], lc.NOTE_DURING_LISTING)

    def test_minutes_accumulate_across_aborted_session(self):
        code, out, err = self.start(["1", 120, "T.a", KeyboardInterrupt])
        self.assertEqual(code, 1)
        self.assertEqual(self.events(self.sha)[-1]["event"], "abort")
        self.assertEqual(self.events(self.sha)[-1]["elapsed_seconds"], "120.0")
        code, out, err = self.start(["1", 60, "T.a", "listed", *N_UNIT, "done", *self.finish()])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.commits()[0]["minutes"], "3.0")

    def test_remaining_timebox_after_abort(self):
        self.assertEqual(self.start(["1", 600, "T.a"])[0], 1)
        code, out, err = self.start(["1", "T.a", "listed", "n", 301, "n", "3", "3", "", *self.finish()])
        self.assertEqual(code, 0, err)
        self.assertIn("timebox: 5.0 minutes remaining", out)
        self.assertIn("TIMEBOX REACHED", out)
        self.assertEqual(self.units()[0]["label"], "N")
        self.assertEqual(self.commits()[0]["minutes"], "15.0")

    def test_exhausted_timebox_path(self):
        self.assertEqual(self.start([900])[0], 1)
        code, out, err = self.start([])
        self.assertEqual(code, 0, err)
        self.assertIn("Timebox exhausted", out)
        (commit,) = self.commits()
        self.assertEqual(
            (commit["unit_count"], commit["minutes"], commit["notes"], commit["browsed"], commit["first_pass_date"]),
            ("0", "15.0", lc.NOTE_EXHAUSTED, "", "2026-11-20"),
        )
        self.assertEqual(self.units(), [])
        self.assertEqual((self.labels / lc.UNIT_LISTS_DIR / f"{self.sha}.txt").read_bytes(), b"")
        self.assertEqual([e["event"] for e in self.events(self.sha)].count("start"), 1)
        self.assertEqual(self.events(self.sha)[-1]["event"], "finish")
        self.assertEqual(self.script.prompts, [])

    def test_ctrl_c_logs_abort_and_writes_no_rows(self):
        code, out, err = self.start(["1", "T.a", "listed", "n", KeyboardInterrupt])
        self.assertEqual(code, 1)
        self.assertIn("is NOT finished", out)
        self.assertIn("still counts against the timebox", out)
        self.assertEqual(self.events(self.sha)[-1]["event"], "abort")
        self.assertFalse((self.labels / lc.UNITS_CSV).exists())
        self.assertFalse((self.labels / lc.COMMITS_CSV).exists())
        self.assertEqual(list((self.labels / lc.UNIT_LISTS_DIR).iterdir()), [])

    def test_clock_starts_when_guards_pass(self):
        self.clock.advance(42)
        code, out, err = self.start([])
        self.assertEqual(code, 1)
        events = self.events(self.sha)
        self.assertEqual([e["event"] for e in events], ["start", "tick", "abort"])
        self.assertEqual([e["elapsed_seconds"] for e in events], ["0.0", "0.0", "0.0"])
        self.assertEqual(events[0]["utc_iso"], "2026-11-20T12:00:42Z")
        self.assertIn(f"packet: spike/packets/acme__widgets/{self.sha}.txt", out)
        self.assertIn("  2. D  tests/test_gone.py  -", out)
        self.assertIn("  3. R  tests/test_old.py  tests/test_new.py", out)
        self.assertTrue(self.script.prompts[0].startswith("entry"))
        self.assertLess(out.index("qualifying test files"), len(out))

    def test_refused_guard_logs_nothing(self):
        code, out, err = self.start([], sha=self.unlisted)
        self.assertEqual(code, 2)
        self.assertFalse(self.labels.exists())


# ---------------------------------------------------------------- ticks


class TickTests(ToolTestCase):
    def crash(self, seconds):
        with self.assertRaises(Crash):
            self.start(["1", seconds, "T.a", Crash])

    def test_one_tick_per_prompt(self):
        self.assertEqual(self.start(self.simple_session())[0], 0)
        ticks = [e for e in self.events(self.sha) if e["event"] == "tick"]
        self.assertEqual(len(ticks), len(self.script.prompts))

    def test_crashed_session_counts_to_last_tick(self):
        self.crash(200)
        state = lc.scan_state(self.labels)
        self.assertEqual(state.accumulated[self.sha], 200.0)
        self.assertIn(self.sha, state.unterminated)
        code, out, err = self.start(self.simple_session())
        self.assertEqual(code, 0, err)
        self.assertIn("counts up to its last tick", out)
        (commit,) = self.commits()
        self.assertEqual(commit["minutes"], "3.3")
        self.assertEqual(commit["notes"], lc.NOTE_UNTERMINATED)

    def test_two_crashes_accumulate(self):
        self.crash(200)
        self.crash(200)
        self.assertEqual(lc.scan_state(self.labels).accumulated[self.sha], 400.0)
        self.assertEqual(self.start(self.simple_session())[0], 0)
        self.assertEqual(self.commits()[0]["minutes"], "6.7")
        self.assertEqual(self.commits()[0]["notes"], lc.NOTE_UNTERMINATED)

    def test_crash_and_abort_reaching_15_minutes_take_exhausted_path(self):
        self.crash(500)
        self.assertEqual(self.start(["1", 400, "T.a"])[0], 1)
        code, out, err = self.start([])
        self.assertEqual(code, 0, err)
        (commit,) = self.commits()
        self.assertEqual(commit["notes"], f"{lc.NOTE_EXHAUSTED}; {lc.NOTE_UNTERMINATED}")
        self.assertEqual((commit["minutes"], commit["unit_count"]), ("15.0", "0"))

    def test_orphan_tick_refuses(self):
        self.labels.mkdir(parents=True)
        lc.append_session_event(self.labels, lc.session_event(self.sha, "tick", START_UTC, 5.0))
        code, out, err = self.run_tool(["next"])
        self.assertEqual(code, 2)
        self.assertIn("tick event outside a session", err)


# ---------------------------------------------------------------- bulk


class BulkTests(ToolTestCase):
    def paste(self, n):
        return ["names", *[f"1 T.u{i}" for i in range(n)], ""]

    def test_bulk_accepted_above_20(self):
        items = [*self.paste(21), "listed", *w_unit(subtype="2", note="same rewrite"), "b", "done", *self.finish()]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        rows = self.units()
        self.assertEqual(len(rows), 21)
        self.assertEqual((rows[0]["bulk"], rows[0]["before_lines"], rows[0]["after_lines"]), ("N", "4", "2"))
        for row in rows[1:]:
            self.assertEqual(
                (row["label"], row["subtype"], row["justified"], row["value_to_trivial"], row["before_lines"], row["after_lines"], row["bulk"], row["notes"]),
                ("WP", "trivial bound", "Y", "N", "", "", "Y", "triggers=none; same rewrite"),
            )
        self.assertEqual(self.commits()[0]["unit_count"], "21")

    def test_bulk_refused_at_20(self):
        code, out, err = self.start([*self.paste(20), "listed", *N_UNIT, "b"])
        self.assertEqual(code, 1)
        self.assertIn("b refused: bulk needs more than 20 entered units; 20 are entered.", out)

    def test_bulk_refused_before_a_unit_is_labeled(self):
        code, out, err = self.start([*self.paste(21), "listed", "b"])
        self.assertEqual(code, 1)
        self.assertIn("b refused: label one unit first", out)


# ---------------------------------------------------------------- redo


class RedoTests(ToolTestCase):
    def test_redo_replaces_label(self):
        items = ["1", "T.a", "listed", *N_UNIT, "done", "n", "", "", "redo 1", *w_unit(("y", "n", "n", "n"), subtype="10"), self.sha[:8]]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        (row,) = self.units()
        self.assertEqual((row["label"], row["subtype"], row["notes"]), ("WC", "test deleted", "triggers=test_deleted"))

    def test_redo_refused_after_timebox(self):
        items = ["1", "T.a", "listed", "n", 901, "n", "3", "3", "", "n", "", "", "redo 1", self.sha[:8]]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        self.assertIn("redo refused: the timebox was reached in this session.", out)
        self.assertEqual(self.units()[0]["label"], "N")

    def test_redo_refused_for_auto_timebox_unit(self):
        code, out, err = self.start(["1", 901, "T.a", "n", "", "", "redo 1", self.sha[:8]])
        self.assertEqual(code, 0, err)
        self.assertIn("redo refused: unit 1 was labeled U/timebox automatically", out)

    def test_redo_refused_for_bulk_unit(self):
        items = ["names", *[f"1 T.u{i}" for i in range(21)], "", "listed", *N_UNIT, "b", "done", "n", "", "", "redo 2", self.sha[:8]]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        self.assertIn("redo refused: unit 2 is bulk-labeled and cannot be redone individually.", out)

    def test_timebox_during_redo_finishes_the_unit(self):
        items = ["1", "T.a", "listed", *N_UNIT, "done", "n", "", "",
                 "redo 1", "w", 901, "n", "n", "n", "n", "1", "y", "n", "4", "2", "", "redo 1", self.sha[:8]]
        code, out, err = self.start(items)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("TIMEBOX REACHED"), 1)
        self.assertLess(out.index("unit 1 relabeled: WP"), out.index("TIMEBOX REACHED"))
        self.assertIn("redo refused: the timebox was reached in this session.", out)
        self.assertEqual(self.units()[0]["label"], "WP")
        self.assertIn("timebox", [e["event"] for e in self.events()])


# ---------------------------------------------------------------- guards and writes


class GuardTests(ToolTestCase):
    def test_sha_not_listed_generic_message(self):
        for sha in ("0" * 40, self.unlisted, "not-a-sha", self.sha.upper()):
            with self.subTest(sha=sha):
                code, out, err = self.start([], sha=sha)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(err, "SHA is not in the pre-registered random or keyword lists.\n")
                self.assertFalse(self.labels.exists())

    def test_missing_packet_refuses(self):
        (self.config.packets_dir / "beta__gadgets" / f"{fake_sha(2)}.txt").unlink()
        code, out, err = self.start([], sha=fake_sha(2))
        self.assertEqual(code, 2)
        self.assertIn(f"packet file not found: spike/packets/beta__gadgets/{fake_sha(2)}.txt", err)
        self.assertIn("Nothing was changed.", err)
        self.assertFalse(self.labels.exists())

    def test_packet_header_mismatch_refuses(self):
        self.write_packet(REPO_A, self.sha, "keyword")
        code, out, err = self.start([])
        self.assertEqual(code, 2)
        self.assertIn("does not match the list entry", err)
        self.assertFalse(self.labels.exists())

    def test_tampered_list_or_stand_in_refuses_start(self):
        stand_in = self.config.rp_config.qualifying[REPO_A].path
        stand_in.write_bytes(b"changed\n")
        code, out, err = self.start([])
        self.assertEqual(code, 2)
        self.assertIn("SHA-256 mismatch", err)
        code, out, err = self.run_tool(["next"])  # next reads only the two lists
        self.assertEqual(code, 0, err)

    def test_finished_commit_cannot_be_started_again(self):
        self.assertEqual(self.start(self.simple_session())[0], 0)
        before = self.snapshot()
        code, out, err = self.start(self.simple_session())
        self.assertEqual(code, 2)
        self.assertIn("already finished and locked; there is no amend.", err)
        self.assertEqual(self.snapshot(), before)

    def test_failure_between_unit_rows_and_commit_row_leaves_detectable_partial(self):
        with mock.patch.object(lc, "append_commit_row", side_effect=OSError(5, "injected failure")):
            code, out, err = self.start(self.simple_session())
        self.assertEqual(code, 2)
        self.assertIn("injected failure", err)
        self.assertIn("partial write", err)
        self.assertEqual(len(self.units()), 1)
        self.assertFalse((self.labels / lc.COMMITS_CSV).exists())
        self.assertTrue((self.labels / lc.UNIT_LISTS_DIR / f"{self.sha}.txt").exists())
        before = self.snapshot()
        for argv in (["start", self.sha], ["start", fake_sha(2)], ["next"], ["status"]):
            with self.subTest(argv=argv):
                code, out, err = self.run_tool(argv, self.simple_session())
                self.assertEqual(code, 2)
                self.assertIn(f"partial write found for {self.sha}", err)
                self.assertEqual(self.snapshot(), before)

    def test_unit_list_without_commit_row_is_partial(self):
        lc.ensure_dirs(self.labels)
        (self.labels / lc.UNIT_LISTS_DIR / f"{fake_sha(3)}.txt").write_bytes(b"")
        code, out, err = self.run_tool(["next"])
        self.assertEqual(code, 2)
        self.assertIn(f"partial write found for {fake_sha(3)}", err)

    def test_finish_commit_write_order(self):
        calls = []
        names = ("write_unit_list", "append_unit_rows", "append_commit_row", "append_session_event")
        patches = [mock.patch.object(lc, n, side_effect=lambda *a, n=n: calls.append(n)) for n in names]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.mark_finished(REPO_A, self.sha, "random")
        self.assertEqual(calls, list(names))


# ---------------------------------------------------------------- practice


class PracticeTests(ToolTestCase):
    def practice_file(self, sha, folder=None):
        return self.write_packet(REPO_A, sha, "random", folder=folder or self.base / "hand")

    def test_practice_writes_nothing(self):
        path = self.practice_file(self.unlisted)
        items = ["1", "T.a", "listed", *w_unit(("n", "n", "y", "n")), "done", "n", "", "", "redo 1", *N_UNIT, "ok"]
        code, out, err = self.run_tool(["practice", str(path)], items)
        self.assertEqual(code, 0, err)
        self.assertIn("packet: " + path.name, out)
        self.assertIn(",".join(lc.UNITS_HEADER), out)
        self.assertIn(",".join(lc.COMMITS_HEADER), out)
        self.assertIn(f"{self.unlisted},random,{REPO_A},tests/test_mod.py,tests/test_mod.py,T.a,N,", out)
        self.assertIn("Practice: nothing was written.", out)
        self.assertFalse(self.labels.exists())

    def test_practice_refuses_file_under_packets(self):
        path = self.practice_file(self.unlisted, folder=self.config.packets_dir / "acme__widgets")
        code, out, err = self.run_tool(["practice", str(path)], ["1", "T.a"])
        self.assertEqual(code, 2)
        self.assertIn("refuses files under spike/packets", err)
        self.assertEqual(self.script.prompts, [])
        self.assertFalse(self.labels.exists())

    def test_practice_refuses_listed_sha(self):
        path = self.practice_file(fake_sha(5))
        code, out, err = self.run_tool(["practice", str(path)], ["1", "T.a"])
        self.assertEqual(code, 2)
        self.assertIn("its sha is in the random or keyword list", err)
        self.assertEqual(self.script.prompts, [])

    def test_practice_refuses_unverifiable_lists(self):
        path = self.practice_file(self.unlisted)
        self.config.rp_config.random_list.path.write_bytes(b"tampered\n")
        code, out, err = self.run_tool(["practice", str(path)], ["1", "T.a"])
        self.assertEqual(code, 2)
        self.assertIn("SHA-256 mismatch", err)
        self.assertEqual(self.script.prompts, [])

    def test_practice_end_of_input_writes_nothing(self):
        path = self.practice_file(self.unlisted)
        code, out, err = self.run_tool(["practice", str(path)], ["1", "T.a"])
        self.assertEqual(code, 1)
        self.assertIn("Practice session ended early. Nothing was written.", out)
        self.assertFalse(self.labels.exists())


# ---------------------------------------------------------------- next and status


class NextStatusTests(ToolTestCase):
    def next_sha(self):
        code, out, err = self.run_tool(["next"])
        self.assertEqual(code, 0, err)
        return out

    def test_next_order_random_then_keyword(self):
        out = self.next_sha()
        self.assertEqual(out, (
            f"finished: 0 of 5; remaining: 5\nnext: {fake_sha(1)}\nstratum: random\nrepo: {REPO_A}\n"
            f"start it with: python -I -B spike/labeling/label_tool.py start {fake_sha(1)}\n"
        ))
        self.mark_finished(REPO_A, fake_sha(1), "random")
        self.mark_finished(REPO_A, fake_sha(3), "random")
        self.assertIn(f"next: {fake_sha(2)}\n", self.next_sha())
        self.mark_finished(REPO_B, fake_sha(2), "random")
        out = self.next_sha()
        self.assertIn(f"next: {fake_sha(4)}\nstratum: keyword\n", out)
        self.assertIn("finished: 3 of 5; remaining: 2", out)
        self.mark_finished(REPO_B, fake_sha(4), "keyword")
        self.mark_finished(REPO_A, fake_sha(5), "keyword")
        self.assertEqual(self.next_sha(), "finished: 5 of 5; remaining: 0\nAll listed commits are finished.\n")

    def test_status(self):
        self.mark_finished(REPO_A, fake_sha(1), "random", minutes="2.5")
        self.mark_finished(REPO_B, fake_sha(4), "keyword", minutes="3.0")
        code, out, err = self.run_tool(["status"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, (
            "finished commits: 2\nby stratum:\n  random: 1\n  keyword: 1\n"
            f"by repo:\n  {REPO_A}: 1\n  {REPO_B}: 1\ntotal minutes: 5.5\n"
        ))

    def test_status_empty(self):
        code, out, err = self.run_tool(["status"])
        self.assertEqual(code, 0, err)
        self.assertIn("finished commits: 0\n", out)
        self.assertIn("total minutes: 0.0\n", out)
        self.assertFalse(self.labels.exists())


# ---------------------------------------------------------------- validation


def good_unit(**changes):
    row = lc.unit_row(
        sha=fake_sha(1), stratum="random", repo=REPO_A, base_path="tests/test_mod.py",
        head_path="tests/test_mod.py", qualified_name="T.a", label="N", value_to_trivial="N",
        before_lines="3", after_lines="3", bulk="N", notes="",
    )
    row.update(changes)
    return row


WP = {"label": "WP", "subtype": "other", "justified": "N", "notes": "triggers=none"}
WC = {"label": "WC", "subtype": "assert removed", "justified": "N", "notes": "triggers=assert_removed"}


class ValidationTests(unittest.TestCase):
    def errors(self, **changes):
        return lc.validate_unit_row(good_unit(**changes), PACKET_FILES)

    def test_valid_rows(self):
        valid = [
            {}, WP, WC,
            {**WP, "bulk": "Y", "before_lines": "", "after_lines": ""},
            {"label": "U/timebox", "value_to_trivial": "", "before_lines": "", "after_lines": "", "notes": lc.NOTE_AUTO_TIMEBOX},
            {"label": "U/unclear", "value_to_trivial": "", "notes": "why"},
            {"base_path": "tests/test_gone.py", "head_path": "", "after_lines": "0"},
            {"base_path": "tests/test_old.py", "head_path": "tests/test_new.py"},
        ]
        for changes in valid:
            with self.subTest(changes=changes):
                self.assertEqual(self.errors(**changes), [])

    def test_every_unit_rule_fails(self):
        invalid = {
            "label not in LABELS": {"label": "X"},
            "WP without subtype": {**WP, "subtype": ""},
            "subtype not frozen": {**WP, "subtype": "made up"},
            "N with subtype": {"subtype": "other"},
            "WP without justified": {**WP, "justified": ""},
            "justified not Y/N": {**WP, "justified": "maybe"},
            "N with justified": {"justified": "Y"},
            "WP without value_to_trivial": {**WP, "value_to_trivial": ""},
            "N without value_to_trivial": {"value_to_trivial": ""},
            "U with value_to_trivial": {"label": "U/unclear", "notes": "x"},
            "U/unresolvable with justified": {"label": "U/unresolvable", "value_to_trivial": "", "justified": "N", "notes": "x"},
            "bulk not Y/N": {"bulk": "X"},
            "negative before_lines": {"before_lines": "-1"},
            "word after_lines": {"after_lines": "two"},
            "blank lines on a normal row": {"before_lines": ""},
            "blank after_lines on U/unclear": {"label": "U/unclear", "value_to_trivial": "", "notes": "x", "after_lines": ""},
            "head_path blank for M": {"head_path": ""},
            "head_path set for D": {"base_path": "tests/test_gone.py", "head_path": "tests/test_gone.py"},
            "R with wrong head_path": {"base_path": "tests/test_old.py", "head_path": "tests/test_old.py"},
            "unknown base_path": {"base_path": "tests/other.py", "head_path": "tests/other.py"},
            "empty qualified_name": {"qualified_name": ""},
            "qualified_name with space": {"qualified_name": "T a"},
            "U/unclear without notes": {"label": "U/unclear", "value_to_trivial": ""},
            "U/unresolvable without notes": {"label": "U/unresolvable", "value_to_trivial": "", "notes": " "},
            "WP without triggers note": {**WP, "notes": "free"},
            "WC with no trigger": {**WC, "notes": "triggers=none"},
            "WP with a trigger": {**WP, "notes": "triggers=test_deleted"},
            "unknown trigger": {**WC, "notes": "triggers=gone"},
            "LF in notes": {"notes": "a\nb"},
            "bad sha": {"sha": "abc"},
            "bad stratum": {"stratum": "other"},
            "bad repo": {"repo": "no-slash"},
        }
        for name, changes in invalid.items():
            with self.subTest(rule=name):
                self.assertNotEqual(self.errors(**changes), [])

    def test_unit_uniqueness_and_columns(self):
        errors = lc.validate_units([good_unit(), good_unit()], PACKET_FILES)
        self.assertTrue(any("not unique" in e for e in errors))
        self.assertEqual(lc.validate_units([good_unit(), good_unit(qualified_name="T.b")], PACKET_FILES), [])
        row = good_unit()
        del row["notes"]
        self.assertNotEqual(lc.validate_unit_row(row, PACKET_FILES), [])
        with self.assertRaises(lc.LabelError):
            lc.unit_row(colour="red")

    def test_commit_rules(self):
        def commit(**changes):
            row = lc.commit_row(sha=fake_sha(1), stratum="random", repo=REPO_A, first_pass_date="2026-11-20",
                                minutes="1.5", browsed="N", helpers_consulted="", unit_count="1", notes="")
            row.update(changes)
            return lc.validate_commit_row(row, [good_unit()])

        self.assertEqual(commit(), [])
        self.assertEqual(commit(helpers_consulted="a.py:3;b/c.py:10"), [])
        exhausted = lc.commit_row(sha=fake_sha(1), stratum="random", repo=REPO_A, first_pass_date="2026-11-20",
                                  minutes="15.0", browsed="", helpers_consulted="", unit_count="0", notes=lc.NOTE_EXHAUSTED)
        self.assertEqual(lc.validate_commit_row(exhausted, []), [])
        invalid = {
            "bad date": {"first_pass_date": "2026-13-01"},
            "blank date outside practice": {"first_pass_date": ""},
            "minutes two decimals": {"minutes": "1.25"},
            "minutes negative": {"minutes": "-1.0"},
            "browsed not Y/N": {"browsed": "X"},
            "browsed blank": {"browsed": ""},
            "absolute helper": {"helpers_consulted": "/abs.py:3"},
            "helper line 0": {"helpers_consulted": "a.py:0"},
            "helper without line": {"helpers_consulted": "a.py"},
            "helper with backslash": {"helpers_consulted": "a" + chr(92) + "b.py:3"},
            "helper not canonical": {"helpers_consulted": "a.py:3; b.py:4"},
            "unit_count mismatch": {"unit_count": "2"},
            "bad sha": {"sha": "x"},
            "bad stratum": {"stratum": "x"},
            "unit row of another commit": {"repo": REPO_B},
        }
        for name, changes in invalid.items():
            with self.subTest(rule=name):
                self.assertNotEqual(commit(**changes), [])
        row = lc.commit_row(sha=fake_sha(1), stratum="random", repo=REPO_A, first_pass_date="", minutes="0.0",
                            browsed="N", helpers_consulted="", unit_count="0", notes="")
        self.assertEqual(lc.validate_commit_row(row, [], practice=True), [])

    def test_parse_helpers(self):
        self.assertEqual(lc.parse_helpers(""), "")
        self.assertEqual(lc.parse_helpers("  "), "")
        self.assertEqual(lc.parse_helpers(" a.py:3 ; b/c.py:10 "), "a.py:3;b/c.py:10")
        for bad in ("a.py", "a.py:x", "/a.py:1", "../a.py:1", "a.py:1;", "a.py:1:2"):
            with self.subTest(text=bad):
                with self.assertRaises(lc.LabelError):
                    lc.parse_helpers(bad)

    def test_parse_render_packet_output(self):
        """The header parser reads what render_packet.render_packet writes."""
        files = [
            {"status": "M", "old": "tests/test_mod.py", "new": "tests/test_mod.py", "similarity": None},
            {"status": "D", "old": "tests/test_gone.py", "new": None, "similarity": None, "blob_unreadable": True},
            {"status": "R", "old": "tests/test_old.py", "new": "tests/test_new.py", "similarity": 87},
        ]
        job = rp.Job(repo=REPO_A, sha=fake_sha(1), stratum="random", parent="0" * 40, files=files, clone=Path("."))
        outputs = [("message\n", 0, False), ("", 0, False), ("-x\n+y\n", 0, False)]
        with mock.patch.object(rp, "git_text", side_effect=outputs):
            text, _ = rp.render_packet(job)
        header = lc.parse_packet_header(text)
        self.assertEqual((header.repo, header.sha, header.stratum), (REPO_A, fake_sha(1), "random"))
        self.assertEqual([(f.status, f.base_path, f.head_path) for f in header.files], [
            ("M", "tests/test_mod.py", "tests/test_mod.py"),
            ("D", "tests/test_gone.py", ""),
            ("R", "tests/test_old.py", "tests/test_new.py"),
        ])

    def test_malformed_packets(self):
        good = packet_text(REPO_A, fake_sha(1), "random")
        bad = {
            "not a packet": "hello\n",
            "count mismatch": packet_text(REPO_A, fake_sha(1), "random", count=5),
            "bad status": packet_text(REPO_A, fake_sha(1), "random", files=(("A", "x.py", "x.py", "-"),)),
            "D with new path": packet_text(REPO_A, fake_sha(1), "random", files=(("D", "x.py", "x.py", "-"),)),
            "bad stratum": packet_text(REPO_A, fake_sha(1), "other"),
            "CR": good.replace("\n", chr(13) + "\n"),
        }
        for name, text in bad.items():
            with self.subTest(case=name):
                with self.assertRaises(lc.LabelError):
                    lc.parse_packet_header(text)

    def test_session_totals(self):
        sha = fake_sha(1)

        def ev(event, seconds):
            return lc.session_event(sha, event, START_UTC, seconds)

        totals, unterminated = lc.session_totals([
            ev("start", 0), ev("tick", 50), ev("abort", 60),
            ev("start", 0), ev("tick", 10), ev("tick", 30),
            ev("start", 0), ev("tick", 5),
        ])
        self.assertEqual(totals[sha], 95.0)
        self.assertEqual(unterminated, {sha})
        with self.assertRaises(lc.LabelError):
            lc.session_totals([ev("abort", 1)])


# ---------------------------------------------------------------- static checks


SOURCES = (COMMON, TOOL, THIS_FILE)


class StaticChecks(unittest.TestCase):
    def test_imports_are_stdlib_only(self):
        for path in SOURCES:
            with self.subTest(file=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                modules = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        self.assertEqual(node.level, 0, "relative import found")
                        modules.append(node.module)
                self.assertTrue(modules)
                for name in modules:
                    self.assertNotIn("detector", name.lower())
                    self.assertIn(name.split(".")[0], sys.stdlib_module_names, name)

    def test_sources_have_no_forbidden_strings(self):
        for path in SOURCES:
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(FORBIDDEN_WORD, text.lower())
                self.assertNotIn(FORBIDDEN_NAME, text)

    def test_sources_bytes(self):
        for path in SOURCES:
            with self.subTest(file=path.name):
                data = path.read_bytes()
                self.assertNotIn(BOM_BYTES, data)
                self.assertNotIn(REPLACEMENT_BYTES, data)
                self.assertNotIn(CR_BYTE, data)
                data.decode("ascii")

    def test_cli_help_runs_isolated(self):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(TOOL), "--help"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for word in (b"next", b"status", b"start", b"practice"):
            self.assertIn(word, result.stdout)


if __name__ == "__main__":
    unittest.main()
