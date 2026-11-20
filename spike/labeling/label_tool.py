"""Gate 0 labeling helper (spike/PREREGISTRATION.md sections 6, 8.2 and 9).

Records first-pass labels for the pre-registered random and keyword commits
and makes the 15-minute timebox and first_pass_date mechanical.

Non-detector spike code: standard library only, imports nothing from the
detector. It loads its two sibling files by file location, because python -I
keeps this folder off sys.path: render_packet.py (the one source of the pinned
list hashes and list parsing) and label_common.py (schemas, validation and
append-only writing). A later relabel tool can reuse run_start() and Session
with a Config whose labels_dir is spike/labels/relabel.

Usage:
  python -I -B spike/labeling/label_tool.py next
  python -I -B spike/labeling/label_tool.py status
  python -I -B spike/labeling/label_tool.py start <sha>
  python -I -B spike/labeling/label_tool.py practice <packet-file>
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import importlib.util
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_sibling(name: str):
    path = HERE / f"{name}.py"
    module = sys.modules.get(name)
    if module is not None and Path(getattr(module, "__file__", "")).resolve() == path:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rp = _load_sibling("render_packet")
lc = _load_sibling("label_common")

SCRIPT_CMD = "python -I -B spike/labeling/label_tool.py"
JUDGEMENTS = ("w", "n", "c", "r")
JUDGEMENT_PROMPT = "judgement for this unit: w = weakened, n = not weakened, c = unclear, r = unresolvable"
NUMBER_RE = re.compile(r"[0-9]+")


class Refusal(Exception):
    """A guard failed before anything was written."""


@dataclass(frozen=True)
class Config:
    """Input and output locations. The command line always uses
    default_config(); only the unit tests pass a different Config."""

    rp_config: object  # render_packet.Config: pinned list and qualifying hashes
    repo_root: Path
    labels_dir: Path
    packets_dir: Path


def default_config() -> Config:
    root = rp.REPO_ROOT
    return Config(rp.default_config(), root, root / "spike" / "labels", root / "spike" / "packets")


class SystemClock:
    def utc_now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class Console:
    def __init__(self, input_fn=None, out=None, err=None):
        self.input_fn = input if input_fn is None else input_fn
        self.out = sys.stdout if out is None else out
        self.err = sys.stderr if err is None else err

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def warn(self, text: str) -> None:
        print(text, file=self.err)


def _strerror(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def show_path(config: Config, path: Path) -> str:
    """Repo-relative POSIX path; just the file name for a file outside the repo."""
    try:
        return Path(path).resolve().relative_to(config.repo_root.resolve()).as_posix()
    except ValueError:
        return Path(path).name


# ---------------------------------------------------------------- inputs


def load_lists(config: Config):
    """The two sample lists only, checked against render_packet's pinned
    hashes and parsed by render_packet.index_lists. Reads no other input."""
    rpc = config.rp_config
    blobs = {}
    for item in (rpc.random_list, rpc.keyword_list):
        try:
            data = item.path.read_bytes()
        except OSError as exc:
            raise Refusal(f"cannot read input file {item.path.name}: {_strerror(exc)}") from exc
        if hashlib.sha256(data).hexdigest() != item.sha256:
            raise Refusal(f"SHA-256 mismatch for input file {item.path.name}")
        blobs[item.path] = data
    try:
        return rp.index_lists(rpc, blobs)
    except rp.GuardError as exc:
        raise Refusal(str(exc)) from exc


def load_state(config: Config):
    """Label state; refuses on unreadable label files or any partial write."""
    try:
        state = lc.scan_state(config.labels_dir)
    except lc.LabelError as exc:
        raise Refusal(f"label files are not as expected: {exc}") from exc
    if state.partial:
        raise Refusal(
            "partial write found for " + ", ".join(state.partial)
            + ": unit rows or a unit_lists file exist without a commit row."
            " The tool never repairs this; it must be fixed by hand."
        )
    return state


def read_packet(path: Path):
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise Refusal(f"cannot read the packet file: {_strerror(exc)}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refusal("the packet file is not valid UTF-8") from exc
    try:
        return lc.parse_packet_header(text)
    except lc.LabelError as exc:
        raise Refusal(f"the packet file is not usable: {exc}") from exc


def write_finished(config: Config, header, unit_rows: list, commit: dict, event: dict) -> None:
    try:
        lc.finish_commit(config.labels_dir, header.files, unit_rows, commit, event)
    except OSError as exc:
        raise lc.LabelError(
            f"writing failed ({_strerror(exc)}). Some files may already be written: if so, every"
            " command now refuses on the partial write until it is repaired by hand."
        ) from exc


# ---------------------------------------------------------------- commands


def cmd_next(config: Config, console: Console) -> int:
    state = load_state(config)
    ordered, _ = load_lists(config)
    remaining = [e for e in ordered if e.sha not in state.finished]
    console.say(f"finished: {len(ordered) - len(remaining)} of {len(ordered)}; remaining: {len(remaining)}")
    if not remaining:
        console.say("All listed commits are finished.")
        return 0
    entry = remaining[0]
    console.say(f"next: {entry.sha}")
    console.say(f"stratum: {entry.stratum}")
    console.say(f"repo: {entry.repo}")
    console.say(f"start it with: {SCRIPT_CMD} start {entry.sha}")
    return 0


def cmd_status(config: Config, console: Console) -> int:
    state = load_state(config)
    rows = state.commits
    for row in rows:
        if lc.ONE_DECIMAL_RE.fullmatch(row["minutes"]) is None:
            raise Refusal(f"{lc.COMMITS_CSV}: minutes of {row['sha']} is not a number with one decimal")
    by_stratum = Counter(row["stratum"] for row in rows)
    by_repo = Counter(row["repo"] for row in rows)
    console.say(f"finished commits: {len(rows)}")
    console.say("by stratum:")
    for stratum in lc.STRATA:
        console.say(f"  {stratum}: {by_stratum.get(stratum, 0)}")
    console.say("by repo:")
    for repo in sorted(by_repo):
        console.say(f"  {repo}: {by_repo[repo]}")
    console.say(f"total minutes: {sum(float(row['minutes']) for row in rows):.1f}")
    return 0


def run_start(config: Config, sha: str, console: Console, clock) -> int:
    # Guard 1: the sha is in the pre-registered lists (pinned hashes).
    try:
        blobs = rp.load_inputs(config.rp_config)
        _, by_sha = rp.index_lists(config.rp_config, blobs)
    except rp.GuardError as exc:
        raise Refusal(str(exc)) from exc
    if rp.SHA_RE.fullmatch(sha) is None or sha not in by_sha:
        console.warn(rp.NOT_LISTED_MSG)
        return 2
    entry = by_sha[sha]
    # Guard 2: the packet exists and belongs to this list entry.
    packet = config.packets_dir / entry.repo.replace("/", "__") / f"{sha}.txt"
    if not packet.is_file():
        raise Refusal(f"packet file not found: {show_path(config, packet)}")
    header = read_packet(packet)
    if (header.sha, header.repo, header.stratum) != (entry.sha, entry.repo, entry.stratum):
        raise Refusal("the packet header does not match the list entry (sha, repo or stratum)")
    # Guard 3: no partial write anywhere; this commit is not finished.
    state = load_state(config)
    if sha in state.finished:
        raise Refusal(f"{sha} is already finished and locked; there is no amend.")
    # Guard 4: the timebox left after earlier sessions.
    accumulated = state.accumulated.get(sha, 0.0)
    unterminated = sha in state.unterminated
    if accumulated >= lc.TIMEBOX_SECONDS:
        return record_exhausted(config, console, clock, header, accumulated, unterminated)
    auto_notes = [lc.NOTE_UNTERMINATED] if unterminated else []
    session = Session(config, console, clock, header, packet, accumulated, write=True, auto_notes=auto_notes)
    return session.run()


def record_exhausted(config, console, clock, header, accumulated, unterminated) -> int:
    notes = lc.NOTE_EXHAUSTED + (f"; {lc.NOTE_UNTERMINATED}" if unterminated else "")
    minutes = lc.format_minutes(accumulated)
    now = clock.utc_now()
    commit = lc.commit_row(
        sha=header.sha, stratum=header.stratum, repo=header.repo,
        first_pass_date=now.date().isoformat(), minutes=minutes, browsed="",
        helpers_consulted="", unit_count="0", notes=notes,
    )
    write_finished(config, header, [], commit, lc.session_event(header.sha, "finish", now, 0.0))
    console.say(f"Timebox exhausted: earlier sessions of {header.sha} already used {minutes} minutes.")
    console.say(f"Recorded it as finished with 0 units (first_pass_date {commit['first_pass_date']}).")
    console.say(f"notes: {notes}")
    return 0


def cmd_practice(config: Config, file_arg: str, console: Console, clock) -> int:
    path = Path(file_arg).resolve()
    if path.is_relative_to(config.packets_dir.resolve()):
        raise Refusal(
            "practice refuses files under spike/packets; use a hand-made non-sample packet-format file."
        )
    load_state(config)
    _, by_sha = load_lists(config)
    if not path.is_file():
        raise Refusal(f"practice file not found: {path.name}")
    header = read_packet(path)
    if header.sha in by_sha:
        raise Refusal("practice refuses this file: its sha is in the random or keyword list.")
    console.say("PRACTICE: nothing will be written (no CSV, no session log, no unit_lists, no first_pass_date).")
    session = Session(config, console, clock, header, path, 0.0, write=False, auto_notes=[])
    return session.run()


# ---------------------------------------------------------------- session


@dataclass
class Unit:
    file_no: int
    base_path: str
    head_path: str
    qualified_name: str
    row: dict | None = None
    auto: bool = False  # labeled U/timebox automatically


class Session:
    """One interactive labeling session. The clock starts when run() is
    called, right after the guards pass. Nothing but session events is
    written before the final confirmation; practice writes nothing at all."""

    def __init__(self, config, console, clock, header, packet_path, accumulated, *, write, auto_notes):
        self.config = config
        self.console = console
        self.clock = clock
        self.header = header
        self.files = header.files
        self.packet_path = packet_path
        self.accumulated = accumulated
        self.budget = lc.TIMEBOX_SECONDS - accumulated
        self.write = write
        self.auto_notes = list(auto_notes)
        self.units: list[Unit] = []
        self.listed = False
        self.timebox_reached = False
        self.template: dict | None = None
        self.t0 = 0.0
        self.finished = False

    # ------------------------------------------------------------ clock and log

    def elapsed(self) -> float:
        return self.clock.monotonic() - self.t0

    def expired(self) -> bool:
        return self.elapsed() >= self.budget

    def log(self, event: str) -> None:
        if self.write:
            row = lc.session_event(self.header.sha, event, self.clock.utc_now(), self.elapsed())
            lc.append_session_event(self.config.labels_dir, row)

    def ask(self, prompt: str) -> str:
        """Every prompt goes through here: a tick with the elapsed time is
        logged first, so a crashed session still counts to its last tick."""
        self.log("tick")
        return self.console.input_fn(prompt)

    def say(self, text: str = "") -> None:
        self.console.say(text)

    def add_note(self, note: str) -> None:
        if note not in self.auto_notes:
            self.auto_notes.append(note)

    def pending(self) -> list[Unit]:
        return [u for u in self.units if u.row is None]

    # ------------------------------------------------------------ run

    def run(self) -> int:
        self.t0 = self.clock.monotonic()
        self.log("start")
        try:
            self.intro()
            outcome = self.entry_phase()
            if outcome == "label":
                outcome = self.label_phase()
            if outcome == "timebox":
                self.apply_timebox()
            return self.finish()
        except (KeyboardInterrupt, EOFError):
            return self.ended_early()
        except lc.LabelError:
            if self.write and not self.finished:
                self.log("abort")
            raise

    def ended_early(self) -> int:
        if self.finished:
            return 0
        if not self.write:
            self.say("")
            self.say("Practice session ended early. Nothing was written.")
            return 1
        self.log("abort")
        minutes = lc.format_minutes(self.accumulated + self.elapsed())
        self.say("")
        self.say(f"Session ended early. Commit {self.header.sha} is NOT finished; nothing but the session log was written.")
        self.say(f"The elapsed time still counts against the timebox when you start it again ({minutes} of {lc.TIMEBOX_MINUTES} minutes used).")
        return 1

    def intro(self) -> None:
        h = self.header
        self.say(f"packet: {show_path(self.config, self.packet_path)}")
        self.say("Open the packet in your editor. It is not printed here.")
        self.say(f"commit: {h.sha}  stratum: {h.stratum}  repo: {h.repo}")
        self.say(f"timebox: {self.budget / 60:.1f} minutes remaining. The clock has started.")
        if lc.NOTE_UNTERMINATED in self.auto_notes:
            self.say("Note: an earlier session of this commit ended without an abort or finish; it counts up to its last tick.")
        self.say("qualifying test files (status, old path, new path):")
        for number, f in enumerate(self.files, start=1):
            self.say(f"  {number}. {f.status}  {f.old}  {f.new or '-'}")
        self.say("List the units first: a file number then the qualified name, or 'names' to paste")
        self.say("'<file number> <qualified name>' lines ending with a blank line. Type 'listed' when the list is complete.")

    # ------------------------------------------------------------ unit entry

    def file_number(self, text: str) -> int | None:
        if NUMBER_RE.fullmatch(text) is None:
            return None
        number = int(text)
        return number if 1 <= number <= len(self.files) else None

    def add_unit(self, number: int, name: str) -> bool:
        if name == "" or lc.QUALIFIED_NAME_RE.fullmatch(name) is None:
            self.say("not added: the qualified name must be non-empty and contain no spaces.")
            return False
        f = self.files[number - 1]
        key = (f.base_path, f.head_path, name)
        if any((u.base_path, u.head_path, u.qualified_name) == key for u in self.units):
            self.say("not added: this unit is already entered.")
            return False
        self.units.append(Unit(number, f.base_path, f.head_path, name))
        if self.listed:
            self.add_note(lc.NOTE_ADDED_AFTER_LISTING)
        self.say(f"unit {len(self.units)}: file {number}, {name}")
        return True

    def enter_one(self, file_text: str) -> None:
        number = self.file_number(file_text)
        if number is None:
            self.say(f"not a file number; the files are numbered 1 to {len(self.files)}.")
            return
        name = self.ask("qualified name (for example TestCart.test_cap): ").strip()
        self.add_unit(number, name)

    def paste_units(self) -> None:
        """Reads until a blank line even if the timebox expires meanwhile, so
        pasted lines never end up as answers to later prompts."""
        self.say("Paste '<file number> <qualified name>' lines; end with a blank line.")
        while True:
            line = self.ask("names> ")
            if line.strip() == "":
                return
            parts = line.split()
            number = self.file_number(parts[0]) if len(parts) == 2 else None
            if number is None:
                self.say("line not added: expected '<file number> <qualified name>'.")
                continue
            self.add_unit(number, parts[1])

    def entry_phase(self) -> str:
        while True:
            if self.expired():
                return "timebox"
            command = self.ask(f"entry [file number | names | listed | done] ({len(self.units)} entered): ").strip()
            if command == "listed":
                self.listed = True
                self.say(f"Unit list closed with {len(self.units)} units. Labeling starts.")
                return "label"
            if command == "done":
                self.listed = True
                if self.pending():
                    self.say(f"done refused: {len(self.pending())} units are not labeled yet. The list is now marked listed; label them first.")
                    return "label"
                return "done"
            if command == "names":
                self.paste_units()
            elif command in JUDGEMENTS or command in ("b", "add"):
                self.say("Labeling is refused until you type listed.")
            elif NUMBER_RE.fullmatch(command):
                self.enter_one(command)
            else:
                self.say("unknown entry command.")

    # ------------------------------------------------------------ labeling

    def ask_yes_no(self, question: str) -> bool:
        while True:
            answer = self.ask(f"{question} (y/n): ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("answer y or n.")

    def ask_count(self, prompt: str) -> str:
        while True:
            answer = self.ask(prompt).strip()
            if lc.COUNT_RE.fullmatch(answer):
                return str(int(answer))
            self.say("enter a non-negative whole number.")

    def ask_subtype(self) -> str:
        for number, subtype in enumerate(lc.SUBTYPES, start=1):
            self.say(f"  {number}. {subtype}")
        while True:
            answer = self.ask(f"subtype number (1-{len(lc.SUBTYPES)}): ").strip()
            if NUMBER_RE.fullmatch(answer) and 1 <= int(answer) <= len(lc.SUBTYPES):
                return lc.SUBTYPES[int(answer) - 1]
            self.say("enter a number from the list.")

    def ask_required_note(self) -> str:
        while True:
            note = self.ask("note (required for this label): ").strip()
            if note:
                return note
            self.say("a note is required.")

    def make_row(self, unit, label, subtype, justified, trivial, before, after, bulk, notes) -> dict:
        h = self.header
        row = lc.unit_row(
            sha=h.sha, stratum=h.stratum, repo=h.repo,
            base_path=unit.base_path, head_path=unit.head_path, qualified_name=unit.qualified_name,
            label=label, subtype=subtype, justified=justified, value_to_trivial=trivial,
            before_lines=before, after_lines=after, bulk=bulk, notes=notes,
        )
        errors = lc.validate_unit_row(row, self.files)
        if errors:
            raise lc.LabelError("internal: an invalid unit row was built: " + "; ".join(errors))
        return row

    def label_unit(self, unit: Unit, judgement: str) -> dict:
        subtype = justified = trivial = ""
        triggers = None
        if judgement == "w":
            self.say("The four count-based triggers (PREREGISTRATION 6.4):")
            triggers = [self.ask_yes_no(q) for q in lc.TRIGGER_QUESTIONS]
            label = lc.derive_weakened_label(triggers)
            self.say(f"label: {label}")
            subtype = self.ask_subtype()
            justified = "Y" if self.ask_yes_no("justified?") else "N"
            trivial = "Y" if self.ask_yes_no("value_to_trivial?") else "N"
        elif judgement == "n":
            label = "N"
            trivial = "Y" if self.ask_yes_no("value_to_trivial?") else "N"
        else:
            label = "U/unclear" if judgement == "c" else "U/unresolvable"
        before = self.ask_count("before_lines (lines of the unit definition in the base version, 0 if missing): ")
        after = self.ask_count("after_lines (lines of the unit definition in the head version, 0 if missing): ")
        if label in lc.NOTE_REQUIRED_LABELS:
            note = self.ask_required_note()
        else:
            note = self.ask("note (optional, Enter for none): ").strip()
        notes = lc.trigger_note(triggers, note) if triggers is not None else note
        return self.make_row(unit, label, subtype, justified, trivial, before, after, "N", notes)

    def bulk(self) -> None:
        pending = self.pending()
        if len(self.units) < lc.BULK_MIN_UNITS:
            self.say(f"b refused: bulk needs more than 20 entered units; {len(self.units)} are entered.")
            return
        if self.template is None:
            self.say("b refused: label one unit first; b copies the fields just entered.")
            return
        if not pending:
            self.say("b refused: no unlabeled units are left.")
            return
        t = self.template
        for unit in pending:
            unit.row = self.make_row(
                unit, t["label"], t["subtype"], t["justified"], t["value_to_trivial"], "", "", "Y", t["notes"]
            )
        self.say(f"bulk: {t['label']} applied to {len(pending)} units (bulk=Y, line counts blank).")

    def label_phase(self) -> str:
        while True:
            if self.expired():
                return "timebox"
            pending = self.pending()
            if pending:
                unit = pending[0]
                self.say(f"unit {self.units.index(unit) + 1} of {len(self.units)}: {unit.qualified_name} (file {unit.file_no}: {unit.base_path})")
                command = self.ask(f"{JUDGEMENT_PROMPT} (or b, add, names, done): ").strip()
            else:
                command = self.ask("all entered units are labeled [done | add | names]: ").strip()
            if command in JUDGEMENTS and pending:
                unit.row = self.label_unit(unit, command)
                self.template = unit.row
            elif command == "b":
                self.bulk()
            elif command == "add":
                self.enter_one(self.ask("file number: ").strip())
            elif command == "names":
                self.paste_units()
            elif command == "done":
                if pending:
                    self.say(f"done refused: {len(pending)} units are not labeled yet.")
                    continue
                return "done"
            else:
                self.say("unknown command.")

    def apply_timebox(self) -> None:
        self.timebox_reached = True
        self.say("TIMEBOX REACHED")
        pending = self.pending()
        for unit in pending:
            unit.row = self.make_row(unit, "U/timebox", "", "", "", "", "", "N", lc.NOTE_AUTO_TIMEBOX)
            unit.auto = True
        if pending:
            self.say(f"{len(pending)} units labeled U/timebox.")
        if not self.listed:
            self.add_note(lc.NOTE_DURING_LISTING)
        self.log("timebox")

    # ------------------------------------------------------------ finish

    def commit_notes(self, free: str) -> str:
        return "; ".join([*self.auto_notes, *([free] if free else [])])

    def ask_helpers(self) -> str:
        while True:
            text = self.ask("helpers_consulted (file:line items separated by ;, Enter for none): ")
            try:
                return lc.parse_helpers(text)
            except lc.LabelError as exc:
                self.say(f"not accepted: {exc}.")

    def print_summary(self, free: str) -> None:
        self.say("summary:")
        self.say("    #  label           subtype              just  triv  before  after  bulk  unit")
        for number, unit in enumerate(self.units, start=1):
            r = unit.row
            self.say(
                f"  {number:>3}  {r['label']:<15} {r['subtype'] or '-':<20} {r['justified'] or '-':<5} "
                f"{r['value_to_trivial'] or '-':<5} {r['before_lines'] or '-':<7} {r['after_lines'] or '-':<6} "
                f"{r['bulk']:<5} {unit.qualified_name} (file {unit.file_no})"
            )
        self.say(f"units: {len(self.units)}; minutes so far: {lc.format_minutes(self.accumulated + self.elapsed())}")
        notes = self.commit_notes(free)
        if notes:
            self.say(f"commit notes: {notes}")

    def redo(self, answer: str) -> None:
        parts = answer.split()
        if len(parts) != 2 or NUMBER_RE.fullmatch(parts[1]) is None:
            self.say("usage: redo <unit number>")
            return
        number = int(parts[1])
        if not 1 <= number <= len(self.units):
            self.say(f"redo refused: there is no unit {number}.")
            return
        unit = self.units[number - 1]
        if unit.auto:
            self.say(f"redo refused: unit {number} was labeled U/timebox automatically and cannot be redone.")
            return
        if unit.row["bulk"] == "Y":
            self.say(f"redo refused: unit {number} is bulk-labeled and cannot be redone individually.")
            return
        if not self.timebox_reached and self.expired():
            self.apply_timebox()
        if self.timebox_reached:
            self.say("redo refused: the timebox was reached in this session.")
            return
        self.say(f"redo unit {number}: {unit.qualified_name} (file {unit.file_no}: {unit.base_path})")
        while True:
            judgement = self.ask(f"{JUDGEMENT_PROMPT}: ").strip()
            if judgement in JUDGEMENTS:
                break
            self.say("enter w, n, c or r.")
        unit.row = self.label_unit(unit, judgement)
        self.say(f"unit {number} relabeled: {unit.row['label']}")
        if self.expired():
            self.apply_timebox()

    def finish(self) -> int:
        self.say("Finish this commit.")
        browsed = "Y" if self.ask_yes_no("browsed the repo?") else "N"
        helpers = self.ask_helpers()
        free = self.ask("commit notes (optional, Enter for none): ").strip()
        while True:
            self.print_summary(free)
            if self.write:
                answer = self.ask("type the first 8 characters of the sha to write, or redo <n>: ").strip()
            else:
                answer = self.ask("type ok to print the rows, or redo <n>: ").strip()
            if answer.split()[:1] == ["redo"]:
                self.redo(answer)
                continue
            if answer == (self.header.sha[:8] if self.write else "ok"):
                break
            self.say("not confirmed; nothing is written yet.")
        return self.write_out(browsed, helpers, free)

    def write_out(self, browsed: str, helpers: str, free: str) -> int:
        h = self.header
        unit_rows = [unit.row for unit in self.units]
        notes = self.commit_notes(free)
        if not self.write:
            commit = lc.commit_row(
                sha=h.sha, stratum=h.stratum, repo=h.repo, first_pass_date="",
                minutes=lc.format_minutes(self.elapsed()), browsed=browsed,
                helpers_consulted=helpers, unit_count=str(len(unit_rows)), notes=notes,
            )
            errors = lc.validate_units(unit_rows, self.files) + lc.validate_commit_row(commit, unit_rows, practice=True)
            if errors:
                raise lc.LabelError("practice rows are invalid: " + "; ".join(errors))
            writer = csv.writer(self.console.out, lineterminator=lc.LF)
            writer.writerow(lc.UNITS_HEADER)
            for row in unit_rows:
                writer.writerow([row[key] for key in lc.UNITS_HEADER])
            self.say("")
            writer.writerow(lc.COMMITS_HEADER)
            writer.writerow([commit[key] for key in lc.COMMITS_HEADER])
            self.say("Practice: nothing was written.")
            self.finished = True
            return 0
        # first_pass_date and minutes are taken here, at the final write.
        now = self.clock.utc_now()
        elapsed = self.elapsed()
        commit = lc.commit_row(
            sha=h.sha, stratum=h.stratum, repo=h.repo, first_pass_date=now.date().isoformat(),
            minutes=lc.format_minutes(self.accumulated + elapsed), browsed=browsed,
            helpers_consulted=helpers, unit_count=str(len(unit_rows)), notes=notes,
        )
        write_finished(self.config, h, unit_rows, commit, lc.session_event(h.sha, "finish", now, elapsed))
        self.finished = True
        labels = show_path(self.config, self.config.labels_dir)
        self.say(f"Written to {labels}: commit {h.sha} finished with {len(unit_rows)} units.")
        self.say(f"first_pass_date {commit['first_pass_date']}; minutes {commit['minutes']}.")
        return 0


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gate 0 labeling helper (first pass).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("next", help="show the next unfinished commit")
    sub.add_parser("status", help="finished commits by stratum and repo, and total minutes")
    start = sub.add_parser("start", help="label one pre-registered commit")
    start.add_argument("sha")
    practice = sub.add_parser("practice", help="rubric calibration on a hand-made non-sample packet; writes nothing")
    practice.add_argument("packet_file")
    return parser


def main(argv: list[str] | None = None, config: Config | None = None, console: Console | None = None, clock=None) -> int:
    args = build_parser().parse_args(argv)
    config = config if config is not None else default_config()
    console = console if console is not None else Console()
    clock = clock if clock is not None else SystemClock()
    try:
        if args.command == "next":
            return cmd_next(config, console)
        if args.command == "status":
            return cmd_status(config, console)
        if args.command == "start":
            return run_start(config, args.sha, console, clock)
        return cmd_practice(config, args.packet_file, console, clock)
    except Refusal as exc:
        console.warn(f"ABORT: {exc}")
        console.warn("Nothing was changed.")
        return 2
    except lc.LabelError as exc:
        console.warn(f"ABORT: {exc}")
        return 2
    except OSError as exc:
        console.warn(f"ABORT: file error: {_strerror(exc)}. Check the label files before going on.")
        return 2
    except KeyboardInterrupt:
        console.warn("Interrupted before a session started. Nothing was logged.")
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
