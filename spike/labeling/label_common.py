"""Shared schemas, validation and append-only writing for the Gate 0 labeling
helper (spike/PREREGISTRATION.md sections 6, 8.2 and 9).

Non-detector spike code: standard library only, imports nothing from the
detector. Every function that reads or writes label files takes labels_dir as
an argument, so the first pass (spike/labels) and a later relabel
(spike/labels/relabel) use this code unchanged.

Write order for one finished commit (finish_commit):
  1. unit_lists/<sha>.txt, exclusive create
  2. the unit rows, appended to units.csv
  3. the commit row, appended to commits.csv LAST (it marks the commit done)
  4. a finish event, appended to session_log.csv
A unit row or a unit_lists file without a commit row is a partial write.
Nothing here deletes, rewrites or repairs a file; files are only appended to.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- constants

LABELS = ("WP", "WC", "N", "U/timebox", "U/unclear", "U/unresolvable")
WEAKENED_LABELS = ("WP", "WC")
U_LABELS = ("U/timebox", "U/unclear", "U/unresolvable")
NOTE_REQUIRED_LABELS = ("U/unclear", "U/unresolvable")

# Frozen subtype list (PREREGISTRATION 6.6), in this exact order.
SUBTYPES = (
    "comparison loosened",
    "trivial bound",
    "exception widened",
    "match removed",
    "tolerance loosened",
    "cases removed",
    "assert removed",
    "skip/xfail added",
    "guard/swallow added",
    "test deleted",
    "other",
)

TIMEBOX_MINUTES = 15
TIMEBOX_SECONDS = TIMEBOX_MINUTES * 60
BULK_MIN_UNITS = 21  # bulk needs more than 20 units

UNITS_HEADER = (
    "sha", "stratum", "repo", "base_path", "head_path", "qualified_name", "label",
    "subtype", "justified", "value_to_trivial", "before_lines", "after_lines", "bulk", "notes",
)
COMMITS_HEADER = (
    "sha", "stratum", "repo", "first_pass_date", "minutes", "browsed",
    "helpers_consulted", "unit_count", "notes",
)
# Extra append-only file, not in the pre-registration: it makes the timebox
# survive aborted and crashed sessions.
SESSION_HEADER = ("sha", "event", "utc_iso", "elapsed_seconds")

UNITS_CSV = "units.csv"
COMMITS_CSV = "commits.csv"
SESSION_CSV = "session_log.csv"
UNIT_LISTS_DIR = "unit_lists"

EVENTS = ("start", "tick", "timebox", "abort", "finish")
STRATA = ("random", "keyword")
YES_NO = ("Y", "N")

# The four count-based triggers (PREREGISTRATION 6.4), in question order.
# WP vs WC is decided only by these (6.5 rule 2).
TRIGGERS = ("test_deleted", "skip_xfail_added", "assert_removed", "assert_count_drop")
TRIGGER_QUESTIONS = (
    "(1) test deleted?",
    "(2) new skip or xfail added?",
    "(3) assertion removed (including one removed and another added)?",
    "(4) net assertion-count drop?",
)
TRIGGER_PREFIX = "triggers="

NOTE_EXHAUSTED = "timebox exhausted across aborted sessions; no units recorded"
NOTE_DURING_LISTING = "timebox_during_listing; list incomplete"
NOTE_ADDED_AFTER_LISTING = "unit_added_after_listing"
NOTE_UNTERMINATED = "unterminated earlier session; counted up to its last tick"
NOTE_AUTO_TIMEBOX = "auto: timebox reached before this unit was labeled"

# Packet header format written by render_packet.py.
PACKET_FIRST_LINE = "PACKET"
FILES_HEADER = "qualifying test files (status, old path, new path, similarity):"
UNITS_NOT_LISTED = "Units are NOT listed here. List them by hand from the diff below."

# Special characters, built without escapes.
CR = chr(13)
LF = chr(10)
TAB = chr(9)
BACKSLASH = chr(92)
BOM = chr(0xFEFF)
BOM_BYTES = bytes.fromhex("efbbbf")
CR_BYTE = bytes([13])
LF_BYTE = bytes([10])

SHA_RE = re.compile(r"[0-9a-f]{40}")
REPO_RE = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
COUNT_RE = re.compile(r"[0-9]+")
ONE_DECIMAL_RE = re.compile(r"[0-9]+\.[0-9]")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
UTC_ISO_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
QUALIFIED_NAME_RE = re.compile(r"[^\s]+")
HELPER_RE = re.compile(r"([^:;\s][^:;]*):([1-9][0-9]*)")


class LabelError(Exception):
    """A guard or validation failed. Messages never contain absolute paths."""


# ---------------------------------------------------------------- packet header


@dataclass(frozen=True)
class PacketFile:
    status: str
    old: str
    new: str | None
    similarity: str

    @property
    def base_path(self) -> str:
        return self.old

    @property
    def head_path(self) -> str:
        """Blank for a D file; the new path otherwise (M: same as old)."""
        return "" if self.new is None else self.new


@dataclass(frozen=True)
class PacketHeader:
    repo: str
    sha: str
    stratum: str
    files: tuple


def parse_packet_header(text: str) -> PacketHeader:
    """Read repo, sha, stratum and the qualifying file entries from the packet
    header. Error messages never quote the packet's content."""
    if text.startswith(BOM):
        text = text[1:]
    if CR in text:
        raise LabelError("packet contains a CR character")
    lines = text.split(LF)
    if not lines or lines[0] != PACKET_FIRST_LINE:
        raise LabelError("file is not in packet format (first line is not PACKET)")
    try:
        start = lines.index(FILES_HEADER)
        end = lines.index(UNITS_NOT_LISTED, start + 1)
    except ValueError as exc:
        raise LabelError("packet header has no qualifying test file block") from exc
    fields = {}
    for line in lines[1:start]:
        key, sep, value = line.partition(": ")
        if sep and key not in fields:
            fields[key] = value
    repo = fields.get("repo", "")
    sha = fields.get("sha", "")
    stratum = fields.get("stratum", "")
    if REPO_RE.fullmatch(repo) is None:
        raise LabelError("packet header repo line is missing or malformed")
    if SHA_RE.fullmatch(sha) is None:
        raise LabelError("packet header sha line is missing or malformed")
    if stratum not in STRATA:
        raise LabelError("packet header stratum is not random or keyword")
    files = []
    for number, line in enumerate(lines[start + 1:end], start=1):
        where = f"packet file entry {number}"
        parts = line.split(TAB)
        if len(parts) == 5 and parts[4] == "blob_unreadable":
            parts = parts[:4]
        if len(parts) != 4:
            raise LabelError(f"{where} does not have 4 tab-separated fields")
        status, old, new, similarity = parts
        if status not in ("M", "D", "R"):
            raise LabelError(f"{where}: status is not M, D or R")
        if old in ("", "-"):
            raise LabelError(f"{where}: old path is missing")
        if status == "M" and new != old:
            raise LabelError(f"{where}: M entry has different old and new paths")
        if status == "D" and new != "-":
            raise LabelError(f"{where}: D entry has a new path")
        if status == "R" and (new in ("", "-") or new == old):
            raise LabelError(f"{where}: R entry paths are not a rename")
        files.append(PacketFile(status, old, None if new == "-" else new, similarity))
    if not files:
        raise LabelError("packet lists no qualifying test files")
    if fields.get("qualifying files") != str(len(files)):
        raise LabelError("packet 'qualifying files' count does not match its file list")
    return PacketHeader(repo, sha, stratum, tuple(files))


# ---------------------------------------------------------------- labels


def derive_weakened_label(triggers) -> str:
    """WC if any of the four count-based triggers fired, else WP."""
    answers = tuple(triggers)
    if len(answers) != len(TRIGGERS) or not all(isinstance(a, bool) for a in answers):
        raise LabelError("exactly four yes/no trigger answers are required")
    return "WC" if any(answers) else "WP"


def trigger_note(triggers, free_text: str) -> str:
    """notes for a weakened unit: triggers=<comma list or none>[; free text]."""
    fired = [name for name, yes in zip(TRIGGERS, triggers) if yes]
    prefix = TRIGGER_PREFIX + (",".join(fired) if fired else "none")
    return f"{prefix}; {free_text}" if free_text else prefix


def triggers_from_note(notes: str) -> list | None:
    """The fired triggers recorded in notes, [] for none, None if malformed."""
    head = notes.split("; ", 1)[0]
    if not head.startswith(TRIGGER_PREFIX):
        return None
    body = head[len(TRIGGER_PREFIX):]
    if body == "none":
        return []
    names = body.split(",")
    if any(name not in TRIGGERS for name in names) or len(set(names)) != len(names):
        return None
    return names


def parse_helpers(text: str) -> str:
    """Validate helpers_consulted: file:line items separated by semicolons,
    blank allowed. Returns the canonical form (items joined by ';')."""
    if CR in text or LF in text:
        raise LabelError("helpers_consulted must be one line")
    stripped = text.strip()
    if stripped == "":
        return ""
    items = []
    for number, raw in enumerate(stripped.split(";"), start=1):
        item = raw.strip()
        match = HELPER_RE.fullmatch(item)
        if match is None:
            raise LabelError(f"helper item {number} is not file:line with a positive line number")
        path = match.group(1)
        if path != path.strip() or path.startswith("/") or BACKSLASH in path or ".." in path.split("/"):
            raise LabelError(f"helper item {number} must be a repo-relative path with forward slashes")
        items.append(item)
    return ";".join(items)


def unit_row(**values) -> dict:
    unknown = set(values) - set(UNITS_HEADER)
    if unknown:
        raise LabelError(f"unknown unit field(s): {', '.join(sorted(unknown))}")
    return {key: values.get(key, "") for key in UNITS_HEADER}


def commit_row(**values) -> dict:
    unknown = set(values) - set(COMMITS_HEADER)
    if unknown:
        raise LabelError(f"unknown commit field(s): {', '.join(sorted(unknown))}")
    return {key: values.get(key, "") for key in COMMITS_HEADER}


# ---------------------------------------------------------------- validation


def _text_errors(row: dict, header: tuple, what: str) -> list[str]:
    if not isinstance(row, dict) or tuple(row.keys()) != header:
        return [f"{what} columns do not match the header"]
    errors = []
    for key in header:
        value = row[key]
        if not isinstance(value, str):
            errors.append(f"{key} is not text")
        elif CR in value or LF in value or BOM in value:
            errors.append(f"{key} contains a line break or BOM")
    return errors


def _path_errors(base: str, head: str, files) -> list[str]:
    candidates = [f for f in files if f.base_path == base]
    if not candidates:
        return ["base_path is not a qualifying test file of this packet"]
    if any(f.head_path == head for f in candidates):
        return []
    if head == "":
        return ["head_path is blank but the file status is not D"]
    if all(f.status == "D" for f in candidates):
        return ["head_path must be blank for a D file"]
    return ["head_path does not match the packet file entry"]


def validate_unit_row(row: dict, files) -> list[str]:
    """Every rule for one units.csv row. Returns a list of error strings."""
    errors = _text_errors(row, UNITS_HEADER, "unit row")
    if errors:
        return errors
    if SHA_RE.fullmatch(row["sha"]) is None:
        errors.append("sha is not 40 lowercase hex characters")
    if row["stratum"] not in STRATA:
        errors.append("stratum is not random or keyword")
    if REPO_RE.fullmatch(row["repo"]) is None:
        errors.append("repo is not owner/name")
    label = row["label"]
    if label not in LABELS:
        errors.append("label is not one of WP, WC, N, U/timebox, U/unclear, U/unresolvable")
    weakened = label in WEAKENED_LABELS
    if weakened:
        if row["subtype"] not in SUBTYPES:
            errors.append("subtype is required for WP and WC and must be from the frozen list")
        if row["justified"] not in YES_NO:
            errors.append("justified must be Y or N for WP and WC")
        fired = triggers_from_note(row["notes"])
        if fired is None:
            errors.append("notes of a WP or WC row must start with triggers=<list or none>")
        elif (label == "WC") != bool(fired):
            errors.append("label does not match the triggers in notes (WC if any fired, WP if none)")
    else:
        if row["subtype"] != "":
            errors.append("subtype must be blank unless the label is WP or WC")
        if row["justified"] != "":
            errors.append("justified must be blank unless the label is WP or WC")
    if weakened or label == "N":
        if row["value_to_trivial"] not in YES_NO:
            errors.append("value_to_trivial must be Y or N for WP, WC and N")
    elif row["value_to_trivial"] != "":
        errors.append("value_to_trivial must be blank for U labels")
    if row["bulk"] not in YES_NO:
        errors.append("bulk must be Y or N")
    for key in ("before_lines", "after_lines"):
        value = row[key]
        if value == "":
            if row["bulk"] != "Y" and label != "U/timebox":
                errors.append(f"{key} may be blank only on bulk or U/timebox rows")
        elif COUNT_RE.fullmatch(value) is None:
            errors.append(f"{key} must be a non-negative integer")
    name = row["qualified_name"]
    if name == "":
        errors.append("qualified_name is empty")
    elif QUALIFIED_NAME_RE.fullmatch(name) is None:
        errors.append("qualified_name must not contain whitespace")
    errors.extend(_path_errors(row["base_path"], row["head_path"], files))
    if label in NOTE_REQUIRED_LABELS and row["notes"].strip() == "":
        errors.append("notes are required for U/unclear and U/unresolvable")
    return errors


def validate_units(rows: list, files) -> list[str]:
    """Row rules plus uniqueness of (base_path, head_path, qualified_name)."""
    errors = []
    seen = set()
    for number, row in enumerate(rows, start=1):
        errors.extend(f"unit {number}: {e}" for e in validate_unit_row(row, files))
        if isinstance(row, dict):
            key = (row.get("base_path"), row.get("head_path"), row.get("qualified_name"))
            if key in seen:
                errors.append(f"unit {number}: (base_path, head_path, qualified_name) is not unique in this commit")
            seen.add(key)
    if len({row.get("sha") for row in rows if isinstance(row, dict)}) > 1:
        errors.append("unit rows belong to more than one sha")
    return errors


def _is_date(text: str) -> bool:
    if DATE_RE.fullmatch(text) is None:
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def validate_commit_row(row: dict, unit_rows: list, *, practice: bool = False) -> list[str]:
    """Every rule for one commits.csv row. first_pass_date may be blank only in
    practice; browsed may be blank only on an exhausted-timebox row."""
    errors = _text_errors(row, COMMITS_HEADER, "commit row")
    if errors:
        return errors
    if SHA_RE.fullmatch(row["sha"]) is None:
        errors.append("sha is not 40 lowercase hex characters")
    if row["stratum"] not in STRATA:
        errors.append("stratum is not random or keyword")
    if REPO_RE.fullmatch(row["repo"]) is None:
        errors.append("repo is not owner/name")
    if not (practice and row["first_pass_date"] == "") and not _is_date(row["first_pass_date"]):
        errors.append("first_pass_date must be a YYYY-MM-DD date")
    if ONE_DECIMAL_RE.fullmatch(row["minutes"]) is None:
        errors.append("minutes must be a non-negative number with one decimal")
    exhausted = row["notes"].startswith(NOTE_EXHAUSTED)
    if row["browsed"] not in YES_NO and not (exhausted and row["browsed"] == ""):
        errors.append("browsed must be Y or N")
    try:
        if parse_helpers(row["helpers_consulted"]) != row["helpers_consulted"]:
            errors.append("helpers_consulted is not in canonical form (file:line;file:line)")
    except LabelError as exc:
        errors.append(f"helpers_consulted: {exc}")
    count = row["unit_count"]
    if COUNT_RE.fullmatch(count) is None or int(count) != len(unit_rows):
        errors.append("unit_count does not equal the number of unit rows")
    for unit in unit_rows:
        if not isinstance(unit, dict) or (unit.get("sha"), unit.get("stratum"), unit.get("repo")) != (
            row["sha"], row["stratum"], row["repo"]
        ):
            errors.append("a unit row's sha, stratum or repo differs from the commit row")
            break
    return errors


def validate_session_event(row: dict) -> list[str]:
    errors = _text_errors(row, SESSION_HEADER, "session event")
    if errors:
        return errors
    if SHA_RE.fullmatch(row["sha"]) is None:
        errors.append("sha is not 40 lowercase hex characters")
    if row["event"] not in EVENTS:
        errors.append("event is not start, tick, timebox, abort or finish")
    if UTC_ISO_RE.fullmatch(row["utc_iso"]) is None:
        errors.append("utc_iso is not YYYY-MM-DDTHH:MM:SSZ")
    if ONE_DECIMAL_RE.fullmatch(row["elapsed_seconds"]) is None:
        errors.append("elapsed_seconds must be a non-negative number with one decimal")
    return errors


# ---------------------------------------------------------------- time formats


def utc_iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_seconds(seconds: float) -> str:
    return f"{max(seconds, 0.0):.1f}"


def format_minutes(seconds: float) -> str:
    return f"{max(seconds, 0.0) / 60:.1f}"


def session_event(sha: str, event: str, moment: datetime.datetime, elapsed: float) -> dict:
    return {
        "sha": sha,
        "event": event,
        "utc_iso": utc_iso(moment),
        "elapsed_seconds": format_seconds(elapsed),
    }


# ---------------------------------------------------------------- reading


def read_csv(path: Path, header: tuple) -> list[dict]:
    """Rows of an existing label CSV as dicts; [] if the file does not exist.
    Refuses a BOM, a CR byte, a missing final LF, bad UTF-8 or a header that
    differs from the expected one."""
    name = path.name
    if not path.exists():
        return []
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise LabelError(f"cannot read {name}: {exc.strerror}") from exc
    if data.startswith(BOM_BYTES):
        raise LabelError(f"{name} starts with a BOM")
    if CR_BYTE in data:
        raise LabelError(f"{name} contains a CR byte")
    if not data.endswith(LF_BYTE):
        raise LabelError(f"{name} is empty or does not end with a line feed")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LabelError(f"{name} is not valid UTF-8") from exc
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows or tuple(rows[0]) != header:
        raise LabelError(f"{name} header does not match the expected columns")
    out = []
    for number, values in enumerate(rows[1:], start=2):
        if len(values) != len(header):
            raise LabelError(f"{name} row {number} has {len(values)} fields, expected {len(header)}")
        out.append(dict(zip(header, values)))
    return out


@dataclass
class LabelState:
    commits: list
    finished: set
    partial: list
    accumulated: dict  # sha -> seconds used by earlier sessions
    unterminated: set  # shas with an earlier session that has no abort/finish


def session_totals(events: list) -> tuple[dict, set]:
    """Seconds used per sha by the sessions in session_log.csv. A session runs
    from a start to the next abort, finish or start of the same sha. An abort
    counts its own elapsed seconds; a session with no abort or finish counts
    up to its last tick (or timebox) event, or 0 if it has none."""
    totals = {}
    unterminated = set()
    open_last = {}
    for number, row in enumerate(events, start=2):
        errors = validate_session_event(row)
        if errors:
            raise LabelError(f"{SESSION_CSV} row {number}: {errors[0]}")
        sha = row["sha"]
        event = row["event"]
        seconds = float(row["elapsed_seconds"])
        totals.setdefault(sha, 0.0)
        if event == "start":
            if sha in open_last:
                totals[sha] += open_last.pop(sha)
                unterminated.add(sha)
            open_last[sha] = 0.0
        elif event in ("tick", "timebox"):
            if sha not in open_last:
                raise LabelError(f"{SESSION_CSV} row {number}: {event} event outside a session")
            open_last[sha] = max(open_last[sha], seconds)
        elif event == "abort":
            if sha not in open_last:
                raise LabelError(f"{SESSION_CSV} row {number}: abort event outside a session")
            open_last.pop(sha)
            totals[sha] += seconds
        else:  # finish; the exhausted-timebox path logs one without a session
            open_last.pop(sha, None)
            totals[sha] += seconds
    for sha, last in open_last.items():
        totals[sha] += last
        unterminated.add(sha)
    return totals, unterminated


def scan_state(labels_dir: Path) -> LabelState:
    """Finished commits, partial writes and time used, from labels_dir."""
    commits = read_csv(labels_dir / COMMITS_CSV, COMMITS_HEADER)
    units = read_csv(labels_dir / UNITS_CSV, UNITS_HEADER)
    events = read_csv(labels_dir / SESSION_CSV, SESSION_HEADER)
    finished = set()
    for row in commits:
        if row["sha"] in finished:
            raise LabelError(f"{COMMITS_CSV} has more than one row for {row['sha']}")
        finished.add(row["sha"])
    partial = {row["sha"] for row in units if row["sha"] not in finished}
    list_dir = labels_dir / UNIT_LISTS_DIR
    if list_dir.is_dir():
        for path in list_dir.iterdir():
            name = path.name
            sha = name[:-4] if name.endswith(".txt") else ""
            if SHA_RE.fullmatch(sha) is None or not path.is_file():
                raise LabelError(f"unexpected entry in {UNIT_LISTS_DIR}: {name}")
            if sha not in finished:
                partial.add(sha)
    accumulated, unterminated = session_totals(events)
    return LabelState(commits, finished, sorted(partial), accumulated, unterminated)


# ---------------------------------------------------------------- writing


def ensure_dirs(labels_dir: Path) -> None:
    labels_dir.mkdir(parents=True, exist_ok=True)
    (labels_dir / UNIT_LISTS_DIR).mkdir(exist_ok=True)


def _append_rows(path: Path, header: tuple, rows: list) -> None:
    exists = path.exists()
    if exists:
        read_csv(path, header)  # refuse to append to a file that is not as expected
    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator=LF)
        if not exists:
            writer.writerow(header)
        for row in rows:
            writer.writerow([row[key] for key in header])


def write_unit_list(labels_dir: Path, sha: str, unit_rows: list) -> None:
    """unit_lists/<sha>.txt: base_path, head_path (- when none), qualified_name,
    tab separated, one line per unit. Exclusive create: never overwritten."""
    lines = []
    for row in unit_rows:
        fields = (row["base_path"], row["head_path"] or "-", row["qualified_name"])
        if any(TAB in f or LF in f or CR in f for f in fields):
            raise LabelError("a unit field contains a tab or line break")
        lines.append(TAB.join(fields) + LF)
    with open(labels_dir / UNIT_LISTS_DIR / f"{sha}.txt", "x", encoding="utf-8", newline="") as handle:
        handle.write("".join(lines))


def append_unit_rows(labels_dir: Path, rows: list) -> None:
    _append_rows(labels_dir / UNITS_CSV, UNITS_HEADER, rows)


def append_commit_row(labels_dir: Path, row: dict) -> None:
    _append_rows(labels_dir / COMMITS_CSV, COMMITS_HEADER, [row])


def append_session_event(labels_dir: Path, row: dict) -> None:
    errors = validate_session_event(row)
    if errors:
        raise LabelError("session event not written: " + "; ".join(errors))
    ensure_dirs(labels_dir)
    _append_rows(labels_dir / SESSION_CSV, SESSION_HEADER, [row])


def finish_commit(labels_dir: Path, files, unit_rows: list, commit: dict, finish_event: dict) -> None:
    """Validate everything, then write in the fixed order (module docstring).
    The commit row goes last but one; the finish event follows it."""
    errors = validate_units(unit_rows, files) + validate_commit_row(commit, unit_rows)
    errors += validate_session_event(finish_event)
    if not errors and (finish_event["event"] != "finish" or finish_event["sha"] != commit["sha"]):
        errors.append("the closing event is not a finish event for this sha")
    if errors:
        raise LabelError("not written: " + "; ".join(errors))
    state = scan_state(labels_dir)
    sha = commit["sha"]
    if sha in state.finished:
        raise LabelError(f"not written: {sha} is already finished")
    if state.partial:
        raise LabelError(f"not written: partial write found for {', '.join(state.partial)}")
    ensure_dirs(labels_dir)
    write_unit_list(labels_dir, sha, unit_rows)
    append_unit_rows(labels_dir, unit_rows)
    append_commit_row(labels_dir, commit)
    append_session_event(labels_dir, finish_event)
