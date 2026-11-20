"""Gate 0 sampling script (Step B of spike/PREREGISTRATION.md, commit f44e44a).

Throwaway spike code under ADR 0003. Not product code and not the engine; it
must never be imported by product code.

Run from anywhere:  python -I -B spike/sampling/build_sample.py
Uses only the Python standard library plus `git` and `gh` as subprocesses.
`gh` is used for read-only `gh api --method GET` calls and one `gh auth status`.

Interpretations this script makes (owner to confirm or change):
 1. Ordering of 4.5: the `import hashlib`/`import re` lines and the two verbatim
    re.compile calls must precede the check. The check runs right after them,
    before any other import, constant, seed, topic list, network or git call.
    It aborts with `raise SystemExit`, not `assert`, so `python -O` can't remove it.
 2. Clone mode: `git clone --no-checkout` (full history, all refs, no working
    tree). R2 reads HEAD files through `git ls-tree`/`git cat-file`, so a Windows
    checkout failure (reserved names, long paths) can't cause a spurious "clone
    failure" skip. R5 measures `.git` in bytes.
 3. 500 MB = 500,000,000 bytes. The exact byte count is recorded.
 4. Bucket membership = the repo appears in that bucket's query results, or its
    returned `topics` contain one of the bucket's topics (1.3's definition).
    Memberships added through topics only are logged.
 5. The owner match and the R7 `pytest-` prefix are compared case-insensitively.
 6. R2 config: root-level pyproject.toml/pytest.ini/setup.cfg/tox.ini, checked
    with a section-header line regex, plus a literal `pytest` substring in
    .github/workflows/*.yml (top level, .yml only). Only the attribute form
    `pytest.raises(...)`/`pytest.warns(...)` is counted. Symlinked test files
    are skipped and counted.
 7. API errors: 404/409/451 on R1/R4 count as a fail. Rate-limit and 5xx errors
    are retried. Any other API error aborts the run, because 1.3 allows no skips
    other than clone failures and timeouts. A missing refs/heads/<default_branch>
    after cloning is logged as a clone failure.
 8. K3 parsing of sample files isn't done in Step B; it belongs to the later K3
    measurement. Step B logs the generated-file exclusions per commit.
 9. Git config overrides that don't change semantics, so user git config can't
    change the output: `-c log.showSignature=false` on git log, and
    `--no-ext-diff --no-textconv --no-color` on the descriptive git diff.
10. Fewer than 2 repos selected: write the snapshot and log, no SHA lists, exit 2.
11. SHA list format: one `<full_name> <sha>` line each, repos in W, D, T order,
    each repo's commits in draw order.
12. If any output file already exists, abort before any network or git call.
    Files are opened with mode "x", so nothing is ever overwritten.

1.3 order notes (code vs doc; reported, not changed):
 a. The cross-bucket rule is wider than the doc's "Assigning" bullet. The doc
    excludes a repo that "appears in more than one bucket" (in the query
    results). The code also counts topics-field membership (interpretation 4),
    so it can exclude more repos than appearance in the results alone would.
 b. The doc doesn't say whether R3 or R7 comes first. The code checks R3 first
    and records only the first filter that fails, so a repo failing both
    appears only under r3_dropped. R3 drops are expected to be 0 because the
    query already has stars:>=1000.
 c. R7 name prefix: the doc says the name "starts with pytest-", which reads as
    case-sensitive. The code compares case-insensitively (interpretation 5).
 d. A repo that is both cross-bucket and R7-failing is logged only as
    cross-bucket. This is consistent with the doc's order (cross-bucket
    exclusion, then R3/R7).
"""
# ---------------------------------------------------------------------------
# §4.5 regex hash lock. This block executes before any other import, constant,
# seed, topic list, network call or git call.
# ---------------------------------------------------------------------------
import hashlib
import re

KEYWORD_RE = re.compile(
    r"\b(?:"
    r"flak(?:y|iness)"
    r"|loosen\w*"
    r"|relax\w*"
    r"|toleran(?:ce|ces|t)"
    r"|weaken\w*"
    r"|xfail\w*"
    r"|skip\w*"
    r"|disabl(?:e|es|ed|ing)\s+tests?"
    r"|fix(?:es|ed|ing)?\s+tests?"
    r"|make\s+tests?\s+pass"
    r"|less\s+strict"
    r"|approx\w*"
    r")\b",
    re.IGNORECASE,
)
CI_SKIP_RE = re.compile(r"\[(?:skip ci|ci skip)\]", re.IGNORECASE)

_EXPECTED_REGEX_SHA256 = "9dd17bc922488091a9a18eea83b1dd417ffd72f5338fb7395cf6da3330cb5cdc"
for _rx in (KEYWORD_RE, CI_SKIP_RE):
    if (_rx.flags & ~re.UNICODE) != re.IGNORECASE:
        raise SystemExit(f"ABORT (prereg 4.5): flags of {_rx.pattern!r} are {int(_rx.flags)}, expected re.IGNORECASE only")
_CANONICAL = "\n".join([
    "KEYWORD_RE", KEYWORD_RE.pattern, "flags=re.IGNORECASE",
    "CI_SKIP_RE", CI_SKIP_RE.pattern, "flags=re.IGNORECASE",
]).encode("utf-8")
_REGEX_DIGEST = hashlib.sha256(_CANONICAL).hexdigest()
if (_REGEX_DIGEST != _EXPECTED_REGEX_SHA256 or len(_CANONICAL) != 278
        or _CANONICAL[0] != 0x4B or _CANONICAL[-1] != 0x45):
    raise SystemExit(f"ABORT (prereg 4.5): regex digest {_REGEX_DIGEST} ({len(_CANONICAL)} bytes) "
                     f"does not match frozen digest {_EXPECTED_REGEX_SHA256} (278 bytes)")
del _rx
# ----------------------------- end of §4.5 check ---------------------------

import ast
import datetime as dt
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
import traceback
import urllib.parse
import warnings
from collections import Counter
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

# §4.3/§4.2 reporting: the 12 alternatives, verified to rebuild the frozen pattern.
KEYWORD_ALTERNATIVES = [
    r"flak(?:y|iness)", r"loosen\w*", r"relax\w*", r"toleran(?:ce|ces|t)",
    r"weaken\w*", r"xfail\w*", r"skip\w*", r"disabl(?:e|es|ed|ing)\s+tests?",
    r"fix(?:es|ed|ing)?\s+tests?", r"make\s+tests?\s+pass", r"less\s+strict", r"approx\w*",
]
if r"\b(?:" + "|".join(KEYWORD_ALTERNATIVES) + r")\b" != KEYWORD_RE.pattern:
    raise SystemExit("ABORT: KEYWORD_ALTERNATIVES do not rebuild KEYWORD_RE.pattern")

# ------------------------------- constants ---------------------------------
PREREG_COMMIT = "f44e44a"
PREREG_PATH = "spike/PREREGISTRATION.md"
SEED_PREFIX = "20261120"
BUCKET_ORDER = ("W", "D", "T")
BUCKET_TOPICS = {  # §1.3, frozen
    "W": ["web-framework", "http", "http-client", "asgi", "wsgi", "rest-api"],
    "D": ["data-science", "dataframe", "scientific-computing", "numpy", "data-analysis"],
    "T": ["cli", "command-line-tool", "linter", "formatter", "developer-tools", "static-analysis"],
}
SEARCH_QUERY_TEMPLATE = "topic:{topic} language:Python stars:>=1000 archived:false fork:false"
SEARCH_PER_PAGE = 100
SEARCH_RESULT_CAP = 1000
SEARCH_SPACING_S = 2.5  # stays under the search API's 30 requests/minute

R1_LICENSES = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0"}
R2_MIN_PYTEST_SHARE = 0.70
R3_MIN_STARS = 1000
R4_SINCE = "2026-08-22T00:00:00Z"
R5_MIN_QUALIFYING = 300
R5_MAX_GIT_BYTES = 500 * 1000 * 1000  # interpretation: MB = 10**6 bytes
R7_TOPICS = {"pytest", "testing", "test-framework", "unittest"}
R7_NAME_PREFIX = "pytest-"
WINDOW_START = int(dt.datetime(2021, 11, 20, tzinfo=dt.timezone.utc).timestamp())
WINDOW_END = int(dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc).timestamp())
CLONE_TIMEOUT_S = 20 * 60

R2_EXCLUDED_DIRS = {"vendor", "third_party", "_vendor", "site-packages", "docs", "examples"}
SNAPSHOT_DIRS = {"__snapshots__", "snapshots"}
BOT_NAME_SUBSTRINGS = ("dependabot", "renovate", "pre-commit-ci", "github-actions", "allcontributors")
GENERATED_MARKERS = ("@generated", "auto-generated", "autogenerated")

ALLOC = {  # §3.1, §3.2, §3.3, in W, D, T order of the selected repos
    3: {"random": [67, 67, 66], "keyword": [15, 15, 15], "reserve": [34, 33, 33]},
    2: {"random": [100, 100], "keyword": [23, 22], "reserve": [50, 50]},
}
STRATUM_SEED_SUFFIX = {"random": "R", "keyword": "K", "reserve": "R2"}

FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

REPO_ROOT = Path(__file__).resolve().parents[2]
SPIKE_DIR = REPO_ROOT / "spike"
OUT_SNAPSHOT = SPIKE_DIR / "pool_snapshot.json"
OUT_LISTS = {
    "random": SPIKE_DIR / "sample_random_shas.txt",
    "keyword": SPIKE_DIR / "sample_keyword_shas.txt",
    "reserve": SPIKE_DIR / "sample_reserve_shas.txt",
}
OUT_LOG = SPIKE_DIR / "build_sample.log"
ALL_OUTPUTS = [OUT_SNAPSHOT, *OUT_LISTS.values(), OUT_LOG]
QUALIFYING_GLOB = "qualifying_*.json"
R5_SIZE_INTERPRETATION = "500 MB = 500,000,000 bytes"
TOPICS_FIELD_NOTE = ("bucket membership via the topics field only applies to repos "
                     "returned by at least one bucket query")


def qualifying_path(full_name: str) -> Path:
    owner, name = full_name.split("/", 1)
    return SPIKE_DIR / f"qualifying_{owner}__{name}.json"

GIT = shutil.which("git")
GH = shutil.which("gh")
ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1",
           GH_NO_UPDATE_NOTIFIER="1", GCM_INTERACTIVE="never")


class Abort(Exception):
    pass


def _scrub_rules():
    rules = []
    for raw, token in ((REPO_ROOT, "<repo>"), (os.environ.get("TEMP"), "%TEMP%"), (Path.home(), "~")):
        if not raw:
            continue
        for s in {str(raw), str(Path(raw).resolve())}:
            for v in {s, s.replace("\\", "/")}:
                rules.append((v, re.compile(re.escape(v), re.IGNORECASE), token))
    rules.sort(key=lambda r: len(r[0]), reverse=True)  # longest first: repo/TEMP before home
    return [(rx, token) for _v, rx, token in rules]


_SCRUB_RULES = _scrub_rules()
HOME_NAME = Path.home().name


def scrub(text: str) -> str:
    """Replace repo root -> <repo>, %TEMP% dir -> %TEMP%, home dir -> ~. Nothing else."""
    for rx, token in _SCRUB_RULES:
        text = rx.sub(lambda _m, t=token: t, text)
    return text


def scrub_obj(obj):
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {scrub_obj(k): scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(v) for v in obj]
    return obj


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Log:
    def __init__(self, path: Path):
        self._f = open(path, "x", encoding="utf-8", newline="\n")

    def __call__(self, msg: str) -> None:
        line = f"{utcnow()} {scrub(msg)}"
        print(line, flush=True)
        self._f.write(line + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


# ------------------------------ subprocesses --------------------------------
def run(args, *, timeout=None, check=True, stdin_bytes=None):
    if args[0] not in (GIT, GH):
        raise Abort(f"refusing to run non-git/gh executable: {args[0]!r}")
    proc = subprocess.run(args, input=stdin_bytes, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, env=ENV)
    if check and proc.returncode != 0:
        raise Abort(f"command failed ({proc.returncode}): {args!r}: "
                    f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc


def git_bytes(repo: Path, *args) -> bytes:
    return run([GIT, "-C", str(repo), *args]).stdout


def git_text(repo: Path, *args) -> str:
    return git_bytes(repo, *args).decode("utf-8", "replace")


class CatFile:
    """Persistent `git cat-file --batch`. Reads blobs; executes nothing."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.p = subprocess.Popen([GIT, "-C", str(repo), "cat-file", "--batch"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=ENV)

    def get(self, spec: str):
        """Blob bytes for `<oid>` or `<rev>:<path>`, or None if missing or not a blob."""
        if "\n" in spec:
            proc = run([GIT, "-C", str(self.repo), "cat-file", "blob", spec], check=False)
            return proc.stdout if proc.returncode == 0 else None
        self.p.stdin.write(spec.encode("utf-8") + b"\n")
        self.p.stdin.flush()
        header = self.p.stdout.readline()
        if not header:
            raise Abort(f"git cat-file --batch died in {self.repo}")
        parts = header.split()
        if len(parts) != 3:  # "<spec> missing" / "ambiguous"
            return None
        data = self.p.stdout.read(int(parts[2]))
        self.p.stdout.read(1)  # trailing LF
        return data if parts[1] == b"blob" else None

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=60)


# --------------------------------- gh api -----------------------------------
_HTTP_STATUS_RE = re.compile(r"\(HTTP (\d{3})\)")


def gh_get(path: str, params: dict, log: Log):
    """Read-only GET. Returns (status, parsed JSON or None)."""
    if not (path == "search/repositories" or path.startswith("repos/")) or "?" in path:
        raise Abort(f"gh_get: endpoint not allowed: {path!r}")
    url = path + ("?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote) if params else "")
    for attempt in range(1, 6):
        try:
            proc = run([GH, "api", "--method", "GET", "-H", "Accept: application/vnd.github+json", url],
                       timeout=120, check=False)
        except subprocess.TimeoutExpired:
            log(f"  gh api timeout (attempt {attempt}) {url}; retrying")
            time.sleep(10 * attempt)
            continue
        if proc.returncode == 0:
            return 200, json.loads(proc.stdout.decode("utf-8"))
        err = proc.stderr.decode("utf-8", "replace").strip()
        m = _HTTP_STATUS_RE.search(err)
        status = int(m.group(1)) if m else None
        if status in (404, 409, 451):
            return status, None
        if status in (403, 429) and "rate limit" in err.lower():
            log(f"  gh api rate-limited (attempt {attempt}) {url}; sleeping 65 s")
            time.sleep(65)
            continue
        if status is None or status >= 500:
            log(f"  gh api error status={status} (attempt {attempt}) {url}: {err}; retrying")
            time.sleep(10 * attempt)
            continue
        raise Abort(f"gh api GET {url} failed with HTTP {status}: {err}")
    raise Abort(f"gh api GET {url} failed after 5 attempts")


# ------------------------------- §1.3 pool ----------------------------------
def search_topic(topic: str, log: Log) -> dict:
    q = SEARCH_QUERY_TEMPLATE.format(topic=topic)
    pages, items, seen = [], [], set()
    total = None
    dup_within_query = 0
    queried_at = None
    for page in range(1, SEARCH_RESULT_CAP // SEARCH_PER_PAGE + 1):
        time.sleep(SEARCH_SPACING_S)
        when = utcnow()
        queried_at = queried_at or when
        status, body = gh_get("search/repositories", {"q": q, "sort": "stars", "order": "desc",
                                                      "per_page": SEARCH_PER_PAGE, "page": page}, log)
        if status != 200:
            raise Abort(f"search for {q!r} page {page} returned HTTP {status}")
        if total is None:
            total = body["total_count"]
        pages.append({"page": page, "requested_at_utc": when, "total_count": body["total_count"],
                      "incomplete_results": body["incomplete_results"], "n_items": len(body["items"])})
        for it in body["items"]:
            fn = it["full_name"]
            if not FULL_NAME_RE.match(fn):
                raise Abort(f"unexpected full_name from API: {fn!r}")
            if fn in seen:
                dup_within_query += 1
                continue
            seen.add(fn)
            items.append({"full_name": fn, "stargazers_count": it["stargazers_count"],
                          "topics": list(it.get("topics") or []), "default_branch": it["default_branch"]})
        if len(body["items"]) < SEARCH_PER_PAGE or page * SEARCH_PER_PAGE >= min(total, SEARCH_RESULT_CAP):
            break
    log(f"  query {q!r}: total_count={total} returned={len(items)} pages={len(pages)} "
        f"dup_within_query={dup_within_query} truncated={total > SEARCH_RESULT_CAP}")
    return {"topic": topic, "query": q, "sort": "stars", "order": "desc", "per_page": SEARCH_PER_PAGE,
            "queried_at_utc": queried_at, "total_count": total, "truncated_by_cap": total > SEARCH_RESULT_CAP,
            "duplicates_within_query": dup_within_query, "pages": pages, "items": items}


def build_buckets(queries: dict, log: Log) -> dict:
    info = {}   # full_name -> item (first seen)
    by_results = {b: set() for b in BUCKET_ORDER}
    for b in BUCKET_ORDER:
        for topic in BUCKET_TOPICS[b]:
            for it in queries[b][topic]["items"]:
                info.setdefault(it["full_name"], it)
                by_results[b].add(it["full_name"])
    members = {b: set(by_results[b]) for b in BUCKET_ORDER}
    added_by_topics = {b: [] for b in BUCKET_ORDER}
    for fn, it in info.items():
        for b in BUCKET_ORDER:
            if fn not in members[b] and set(it["topics"]) & set(BUCKET_TOPICS[b]):
                members[b].add(fn)
                added_by_topics[b].append(fn)
    count = Counter(fn for b in BUCKET_ORDER for fn in members[b])
    cross = sorted(fn for fn, c in count.items() if c > 1)
    log(f"pool: {len(info)} distinct repos; cross-bucket excluded: {len(cross)}")
    log(f"note: {TOPICS_FIELD_NOTE}")
    out = {"notes": [TOPICS_FIELD_NOTE], "cross_bucket_excluded": [{"full_name": fn, "buckets": [b for b in BUCKET_ORDER if fn in members[b]]}
                                     for fn in cross], "buckets": {}}
    for b in BUCKET_ORDER:
        r3_drop, r7_drop, keep = [], [], []
        for fn in sorted(members[b]):
            if fn in cross:
                continue
            it = info[fn]
            if it["stargazers_count"] < R3_MIN_STARS:
                r3_drop.append({"full_name": fn, "stargazers_count": it["stargazers_count"]})
                continue
            bad_topics = sorted(set(it["topics"]) & R7_TOPICS)
            name = fn.split("/", 1)[1]
            if bad_topics or name.lower().startswith(R7_NAME_PREFIX):
                r7_drop.append({"full_name": fn, "topics": bad_topics,
                                "name_prefix": name.lower().startswith(R7_NAME_PREFIX)})
                continue
            keep.append(fn)
        sorted_names = sorted(keep)
        seed = f"{SEED_PREFIX}-{b}"
        shuffled = list(sorted_names)
        random.Random(seed).shuffle(shuffled)
        log(f"bucket {b}: from_results={len(by_results[b])} added_by_topics_field={len(added_by_topics[b])} "
            f"after_cross_exclusion={len(members[b] - set(cross))} r3_dropped={len(r3_drop)} "
            f"r7_dropped={len(r7_drop)} remaining={len(keep)} seed={seed!r}")
        out["buckets"][b] = {"topics": BUCKET_TOPICS[b], "members_from_results": sorted(by_results[b]),
                             "members_added_by_topics_field": sorted(added_by_topics[b]),
                             "r3_dropped": r3_drop, "r7_dropped": r7_drop, "sorted": sorted_names,
                             "seed": seed, "shuffled_order": shuffled}
    out["repo_info"] = {fn: info[fn] for fn in sorted(info)}
    return out


# ------------------------------- §1.3 walk ----------------------------------
def check_r1(fn: str, log: Log) -> dict:
    status, body = gh_get(f"repos/{fn}/license", {}, log)
    spdx = ((body or {}).get("license") or {}).get("spdx_id") if status == 200 else None
    return {"http_status": status, "spdx_id": spdx, "pass": spdx in R1_LICENSES}


def check_r4(fn: str, branch: str, log: Log) -> dict:
    status, body = gh_get(f"repos/{fn}/commits", {"sha": branch, "since": R4_SINCE, "per_page": 1}, log)
    n = len(body) if status == 200 and isinstance(body, list) else 0
    return {"http_status": status, "since": R4_SINCE, "commits_returned": n, "pass": n >= 1}


def clone_repo(fn: str, dest: Path, log: Log) -> dict:
    args = [GIT, "-c", "core.longpaths=true", "clone", "--no-checkout", "--quiet",
            f"https://github.com/{fn}.git", str(dest)]
    started = time.monotonic()
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=ENV)
    try:
        _, err = proc.communicate(timeout=CLONE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return {"result": "timeout", "seconds": round(time.monotonic() - started, 1), "path": str(dest)}
    if proc.returncode != 0:
        return {"result": "failed", "returncode": proc.returncode, "path": str(dest),
                "stderr": err.decode("utf-8", "replace").strip()[-2000:]}
    return {"result": "ok", "seconds": round(time.monotonic() - started, 1), "path": str(dest)}


def count_sites(tree: ast.AST):
    pytest_sites = unittest_sites = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            pytest_sites += 1
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name)):
            base, attr = node.func.value.id, node.func.attr
            if base == "pytest" and attr in ("raises", "warns"):
                pytest_sites += 1
            elif base == "self" and (attr.startswith("assert") or attr.startswith("fail")):
                unittest_sites += 1
    return pytest_sites, unittest_sites


def decode_source(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    return text[1:] if text.startswith("\ufeff") else text


def pytest_config_evidence(entries, cat: CatFile) -> list:
    root = {path: oid for mode, typ, oid, path in entries if typ == "blob" and "/" not in path}
    evidence = []
    header_rules = [("pyproject.toml", r"(?m)^[ \t]*\[tool\.pytest\.ini_options\]"),
                    ("setup.cfg", r"(?m)^[ \t]*\[tool:pytest\]"),
                    ("tox.ini", r"(?m)^[ \t]*\[pytest\]")]
    for name, pattern in header_rules:
        if name in root and re.search(pattern, decode_source(cat.get(root[name]) or b"")):
            evidence.append(name)
    if "pytest.ini" in root:
        evidence.append("pytest.ini")
    for mode, typ, oid, path in entries:
        p = PurePosixPath(path)
        if typ == "blob" and str(p.parent) == ".github/workflows" and fnmatchcase(p.name, "*.yml"):
            if "pytest" in decode_source(cat.get(oid) or b""):
                evidence.append(path)
    return evidence


def check_r2(repo: Path, head: str, cat: CatFile) -> dict:
    raw = git_bytes(repo, "ls-tree", "-r", "-z", "--full-tree", head)
    entries = []
    for rec in raw.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, typ, oid = meta.split(b" ")
        entries.append((mode.decode(), typ.decode(), oid.decode(), path.decode("utf-8", "replace")))
    pytest_sites = unittest_sites = parsed = symlinks = 0
    unparseable = []
    for mode, typ, oid, path in entries:
        p = PurePosixPath(path)
        if typ != "blob" or not (fnmatchcase(p.name, "test_*.py") or fnmatchcase(p.name, "*_test.py")):
            continue
        if any(d in R2_EXCLUDED_DIRS for d in p.parts[:-1]):
            continue
        if mode == "120000":
            symlinks += 1
            continue
        text = decode_source(cat.get(oid) or b"")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError, RecursionError) as exc:
            unparseable.append({"path": path, "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        parsed += 1
        a, b = count_sites(tree)
        pytest_sites += a
        unittest_sites += b
    total = pytest_sites + unittest_sites
    share = pytest_sites / total if total else None
    evidence = pytest_config_evidence(entries, cat)
    return {"head_sha": head, "test_files_parsed": parsed, "unparseable_count": len(unparseable),
            "unparseable": unparseable, "symlinked_test_files_skipped": symlinks,
            "pytest_sites": pytest_sites, "unittest_sites": unittest_sites, "pytest_share": share,
            "config_evidence": evidence,
            "pass": share is not None and share >= R2_MIN_PYTEST_SHARE and bool(evidence)}


# -------------------------- §2 test-modifying commits -----------------------
_GIT_ESC = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote_git_path(s: str) -> str:
    if not (len(s) >= 2 and s[0] == '"' and s[-1] == '"'):
        return s
    body, out, i = s[1:-1], bytearray(), 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out += c.encode("utf-8")
            i += 1
        elif body[i + 1] in _GIT_ESC:
            out.append(_GIT_ESC[body[i + 1]])
            i += 2
        elif body[i + 1] in "01234567":
            out.append(int(body[i + 1:i + 4], 8))
            i += 4
        else:
            raise Abort(f"cannot unquote git path {s!r}")
    return out.decode("utf-8", "replace")


def test_path_reason(path: str):
    """None if `path` is a §2.3 test file; otherwise why not."""
    p = PurePosixPath(path)
    base, dirs = p.name, p.parts[:-1]
    if not (fnmatchcase(base, "test_*.py") or fnmatchcase(base, "*_test.py")):
        return "not_test"
    if fnmatchcase(base, "snap_*.py") or any(d in SNAPSHOT_DIRS for d in dirs):
        return "snapshot"
    if any(d in R2_EXCLUDED_DIRS for d in dirs):
        return "excluded_dir"
    return None


def is_bot(name: str, email: str) -> bool:
    low = name.lower()
    return (name.endswith("[bot]") or email.lower().endswith("[bot]@users.noreply.github.com")
            or any(s in low for s in BOT_NAME_SUBSTRINGS))


def is_generated(data: bytes) -> bool:
    head = "\n".join(decode_source(data).split("\n")[:5]).lower()
    return any(m in head for m in GENERATED_MARKERS)


def analyse_commit(repo: Path, cat: CatFile, sha: str, parent: str) -> dict:
    out = git_text(repo, "-c", "core.quotepath=false", "diff-tree", "-r", "--no-commit-id", "-M50%",
                   "--diff-filter=MDR", "--name-status", parent, sha)
    counts, cand, saw_snapshot = Counter(), [], False
    for line in out.split("\n"):
        if not line:
            continue
        fields = line.split("\t")
        st = fields[0]
        if st in ("M", "D") and len(fields) == 2:
            path = unquote_git_path(fields[1])
            reason = test_path_reason(path)
            if reason is None:
                cand.append({"status": st, "old": path, "new": path if st == "M" else None, "similarity": None})
            elif reason == "snapshot":
                saw_snapshot = True
        elif st.startswith("R") and len(fields) == 3:
            sim = int(st[1:])
            old, new = unquote_git_path(fields[1]), unquote_git_path(fields[2])
            ro, rn = test_path_reason(old), test_path_reason(new)
            if ro is None and rn is None:
                if sim < 100:
                    cand.append({"status": "R", "old": old, "new": new, "similarity": sim})
                else:
                    counts["test_renames_r100_not_qualifying"] += 1
            else:
                if (ro is None) != (rn is None):
                    counts["test_renames_one_side_not_test"] += 1
                if "snapshot" in (ro, rn):
                    saw_snapshot = True
        else:
            raise Abort(f"unexpected diff-tree line in {repo.name} {sha}: {line!r}")
    kept, ignored = [], []
    for f in cand:
        blobs = [cat.get(f"{parent}:{f['old']}")]
        if f["status"] != "D":
            blobs.append(cat.get(f"{sha}:{f['new']}"))
        if any(b is None for b in blobs):
            counts["qualifying_file_blob_unreadable"] += 1
            f["blob_unreadable"] = True
        (ignored if any(b is not None and is_generated(b) for b in blobs) else kept).append(f)
    if not cand:
        counts["snapshot_only_commits" if saw_snapshot else "no_test_file_change"] += 1
    elif not kept:
        counts["all_test_files_generated"] += 1
    counts["generated_files_ignored"] += len(ignored)
    return {"kept": kept, "ignored": ignored, "counts": counts}


def analyse_history(repo: Path, head: str, log: Log) -> dict:
    stats, bot_names = Counter(), Counter()
    stats["merges_excluded"] = int(git_text(repo, "rev-list", "--count", "--merges", head).strip())
    raw = git_text(repo, "rev-list", "--no-merges", "--format=%H%x00%P%x00%ct%x00%an%x00%ae", head)
    records = [ln for ln in raw.split("\n") if ln and not ln.startswith("commit ")]
    qualifying, generated_log = [], []
    cat = CatFile(repo)
    try:
        for i, rec in enumerate(records, 1):
            sha, parents, ct, an, ae = rec.split("\x00")
            stats["walked_non_merge"] += 1
            if not (WINDOW_START <= int(ct) < WINDOW_END):
                stats["outside_window"] += 1
                continue
            stats["kept_in_window"] += 1
            plist = parents.split()
            if not plist:
                stats["excluded_root"] += 1
                continue
            if len(plist) != 1:
                raise Abort(f"rev-list --no-merges returned a merge commit {sha}")
            if is_bot(an, ae):
                stats["excluded_bot"] += 1
                bot_names[an] += 1
                continue
            res = analyse_commit(repo, cat, sha, plist[0])
            stats.update(res["counts"])
            if res["ignored"]:
                generated_log.append({"sha": sha, "ignored_files": res["ignored"]})
            if res["kept"]:
                stats["qualifying"] += 1
                d_only = all(f["status"] == "D" for f in res["kept"])
                stats["qualifying_d_only"] += d_only
                qualifying.append({"sha": sha, "parent": plist[0], "d_only": d_only, "files": res["kept"]})
            if i % 2000 == 0:
                log(f"    ... {i}/{len(records)} commits walked, {stats['qualifying']} qualifying")
    finally:
        cat.close()
    log(f"    history: {dict(sorted(stats.items()))}")
    log(f"    excluded bot author names: {dict(sorted(bot_names.items()))}")
    return {"stats": dict(sorted(stats.items())), "bot_author_names": dict(sorted(bot_names.items())),
            "generated_exclusions": generated_log, "qualifying": qualifying}


def dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            total += os.lstat(os.path.join(root, name)).st_size
    return total


def check_r5(repo: Path, head: str, log: Log):
    git_size = dir_bytes(repo / ".git")
    if git_size > R5_MAX_GIT_BYTES:
        return {"git_dir_bytes": git_size, "pass": False, "reason": "git_dir_too_large"}, None
    hist = analyse_history(repo, head, log)
    n = hist["stats"].get("qualifying", 0)
    return ({"git_dir_bytes": git_size, "qualifying_commits": n, "pass": n >= R5_MIN_QUALIFYING,
             "reason": None if n >= R5_MIN_QUALIFYING else "too_few_qualifying"}, hist)


def walk_bucket(bucket: str, order: list, repo_info: dict, selected_owners: set,
                clone_root: Path, log: Log):
    attempts = []
    for fn in order:
        owner = fn.split("/", 1)[0]
        branch = repo_info[fn]["default_branch"]
        rec = {"full_name": fn, "default_branch": branch}
        attempts.append(rec)
        if owner.lower() in selected_owners:
            rec["result"] = "skipped_owner_already_selected"
            log(f"  {fn}: skipped, owner already selected")
            continue
        r1 = check_r1(fn, log)
        rec["R1"] = r1
        if not r1["pass"]:
            rec["result"] = "fail_R1"
            log(f"  {fn}: fail R1 (spdx_id={r1['spdx_id']}, http={r1['http_status']})")
            continue
        r4 = check_r4(fn, branch, log)
        rec["R4"] = r4
        if not r4["pass"]:
            rec["result"] = "fail_R4"
            log(f"  {fn}: fail R4 (http={r4['http_status']})")
            continue
        dest = clone_root / fn.replace("/", "__")
        log(f"  {fn}: cloning to {dest}")
        cl = clone_repo(fn, dest, log)
        rec["clone"] = cl
        if cl["result"] != "ok":
            rec["result"] = f"clone_{cl['result']}"
            log(f"  {fn}: clone {cl['result']}, skipped")
            continue
        head_proc = run([GIT, "-C", str(dest), "rev-parse", "--verify", "--quiet",
                         f"refs/heads/{branch}^{{commit}}"], check=False)
        if head_proc.returncode != 0:
            rec["result"] = "clone_failed"
            rec["clone"]["detail"] = "default branch missing after clone"
            log(f"  {fn}: default branch {branch!r} missing after clone, skipped as clone failure")
            continue
        head = head_proc.stdout.decode().strip()
        rec["head_sha"] = head
        cat = CatFile(dest)
        try:
            r2 = check_r2(dest, head, cat)
        finally:
            cat.close()
        rec["R2"] = r2
        log(f"  {fn}: R2 pytest={r2['pytest_sites']} unittest={r2['unittest_sites']} share={r2['pytest_share']} "
            f"parsed={r2['test_files_parsed']} unparseable={r2['unparseable_count']} config={r2['config_evidence']}")
        if not r2["pass"]:
            rec["result"] = "fail_R2"
            continue
        r5, hist = check_r5(dest, head, log)
        rec["R5"] = r5
        if hist is not None:
            rec["history_stats"] = hist["stats"]
        log(f"  {fn}: R5 {r5}")
        if not r5["pass"]:
            rec["result"] = "fail_R5"
            continue
        rec["result"] = "selected"
        log(f"  bucket {bucket}: SELECTED {fn} @ {head}")
        return attempts, {"bucket": bucket, "full_name": fn, "clone_path": str(dest), "head_sha": head,
                          "spdx_id": r1["spdx_id"], "R2": r2, "R5": r5, "history": hist}
    log(f"  bucket {bucket}: UNFILLED")
    return attempts, None


# ------------------------------ §3 sampling ---------------------------------
def draw(pool: list, d_only: dict, seed: str, alloc: int, d_cap: int) -> dict:
    order = sorted(pool)
    random.Random(seed).shuffle(order)
    taken, skipped, d_taken = [], [], 0
    for sha in order:
        if len(taken) >= alloc:
            break
        if d_only[sha]:
            if d_taken >= d_cap:
                skipped.append(sha)
                continue
            d_taken += 1
        taken.append(sha)
    return {"seed": seed, "allocation": alloc, "d_only_cap": d_cap, "eligible": len(order),
            "shuffled_order": order, "taken": taken, "d_only_skipped": skipped,
            "d_only_taken": d_taken, "shortfall": alloc - len(taken),
            "d_only_share": (d_taken / len(taken)) if taken else None}


def classify_alternative(matched: str) -> str:
    for alt in KEYWORD_ALTERNATIVES:
        if re.fullmatch(alt, matched, re.IGNORECASE):
            return alt
    return "<unclassified>"


def keyword_scan(repo: Path, shas: list) -> dict:
    eligible, replaced = [], 0
    occurrences, commits_per_alt = Counter(), Counter()
    for sha in sorted(shas):
        raw = git_bytes(repo, "-c", "log.showSignature=false", "log", "-1", "--format=%B", sha)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            replaced += 1
        text = CI_SKIP_RE.sub(" ", text)
        if KEYWORD_RE.search(text):
            eligible.append(sha)
            alts = [classify_alternative(m.group(0)) for m in KEYWORD_RE.finditer(text)]
            occurrences.update(alts)
            commits_per_alt.update(set(alts))
    return {"messages_scanned": len(shas), "messages_needing_replacement": replaced,
            "eligible": eligible, "match_occurrences_per_alternative": dict(occurrences),
            "commits_per_alternative": dict(commits_per_alt)}


def changes_assert_line(repo: Path, parent: str, sha: str, files: list) -> bool:
    groups = [[f["old"], f["new"]] if f["status"] == "R" else [f["old"]]
              for f in files if f["status"] in ("M", "R")]
    for i in range(0, len(groups), 100):
        paths = [":(literal)" + p for g in groups[i:i + 100] for p in g]
        text = git_text(repo, "-c", "core.quotepath=false", "diff", "-U0", "-M50%", "--no-color",
                        "--no-ext-diff", "--no-textconv", parent, sha, "--", *paths)
        in_hunk = False
        for line in text.split("\n"):
            if line.startswith("diff --git "):
                in_hunk = False
            elif line.startswith("@@"):
                in_hunk = True
            elif in_hunk and line and line[0] in "+-" and line[1:].strip().startswith("assert"):
                return True
    return False


def sample_repo(sel: dict, idx: int, n_repos: int, log: Log) -> dict:
    fn, repo = sel["full_name"], Path(sel["clone_path"])
    qual = sel["history"]["qualifying"]
    d_only = {c["sha"]: c["d_only"] for c in qual}
    all_shas = [c["sha"] for c in qual]
    a = {k: ALLOC[n_repos][k][idx] for k in ALLOC[n_repos]}
    seed = lambda s: f"{SEED_PREFIX}-{fn}-{STRATUM_SEED_SUFFIX[s]}"
    rnd = draw(all_shas, d_only, seed("random"), a["random"], a["random"] // 10)
    in_rnd = set(rnd["taken"])
    kw = keyword_scan(repo, [s for s in all_shas if s not in in_rnd])
    kwd = draw(kw["eligible"], d_only, seed("keyword"), a["keyword"], 1)
    in_kw = set(kwd["taken"])
    res = draw([s for s in all_shas if s not in in_rnd and s not in in_kw], d_only,
               seed("reserve"), a["reserve"], a["reserve"] // 10)
    for name, d in (("random", rnd), ("keyword", kwd), ("reserve", res)):
        log(f"  {fn} {name}: alloc={d['allocation']} eligible={d['eligible']} taken={len(d['taken'])} "
            f"shortfall={d['shortfall']} d_only_cap={d['d_only_cap']} d_only_taken={d['d_only_taken']} "
            f"d_only_skipped={len(d['d_only_skipped'])} d_only_share={d['d_only_share']}")
    log(f"  {fn} keyword scan: scanned={kw['messages_scanned']} replaced={kw['messages_needing_replacement']} "
        f"eligible={len(kw['eligible'])} per_alternative={kw['match_occurrences_per_alternative']}")
    assert_commits = sum(changes_assert_line(repo, c["parent"], c["sha"], c["files"]) for c in qual)
    log(f"  {fn} descriptive (2.6): qualifying commits changing an assert line = {assert_commits}")
    return {"random": rnd, "keyword": dict(kwd, scan={k: v for k, v in kw.items() if k != "eligible"}),
            "reserve": res, "descriptive_assert_line_commits": assert_commits}


# --------------------------------- main -------------------------------------
def write_text(path: Path, text: str) -> None:
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(text)


def preflight() -> dict:
    existing = [str(p) for p in ALL_OUTPUTS if p.exists()] + [str(p) for p in SPIKE_DIR.glob(QUALIFYING_GLOB)]
    if existing:
        raise SystemExit(f"ABORT: output files already exist, remove them first: {existing}")
    if not GIT or not GH:
        raise SystemExit(f"ABORT: git={GIT!r} gh={GH!r}; both must be on PATH")
    if run([GH, "auth", "status", "--hostname", "github.com"], timeout=60, check=False).returncode != 0:
        raise SystemExit("ABORT: `gh auth status` failed; gh is not authenticated to github.com. "
                         "Run `gh auth login` and retry.")
    top = run([GIT, "-C", str(REPO_ROOT), "rev-parse", "--show-toplevel"]).stdout.decode().strip()
    if Path(top).resolve() != REPO_ROOT:
        raise SystemExit(f"ABORT: script is not at spike/sampling/ inside the repo ({top} != {REPO_ROOT})")
    prereg_full = run([GIT, "-C", str(REPO_ROOT), "rev-parse", "--verify",
                       f"{PREREG_COMMIT}^{{commit}}"]).stdout.decode().strip()
    if run([GIT, "-C", str(REPO_ROOT), "diff", "--quiet", prereg_full, "--", PREREG_PATH],
           check=False).returncode != 0:
        raise SystemExit(f"ABORT: {PREREG_PATH} differs from commit {prereg_full}")
    temp = os.environ.get("TEMP")
    if not temp:
        raise SystemExit("ABORT: %TEMP% is not set")
    clone_root = Path(temp).resolve() / f"provecode-gate0-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}"
    if clone_root.is_relative_to(REPO_ROOT):
        raise SystemExit(f"ABORT: clone root {clone_root} is inside the repo")
    return {"prereg_commit": prereg_full, "clone_root": clone_root,
            "repo_head": run([GIT, "-C", str(REPO_ROOT), "rev-parse", "HEAD"]).stdout.decode().strip()}


_LOG_OPENED = False  # set by main() once this run has created OUT_LOG


def main() -> int:
    global _LOG_OPENED
    sys.stdout.reconfigure(errors="backslashreplace")
    try:
        pf = preflight()
    except Abort as exc:
        raise SystemExit(f"ABORT (preflight): {exc}")
    log = Log(OUT_LOG)
    _LOG_OPENED = True
    try:
        return _main(pf, log)
    except Abort as exc:
        log(f"ABORT: {exc}")
        return 1
    finally:
        log.close()


def _main(pf: dict, log: Log) -> int:
    started = utcnow()
    run_info = {
        "started_utc": started,
        "preregistration_commit": pf["prereg_commit"],
        "provecode_head": pf["repo_head"],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "regex_sha256": _REGEX_DIGEST,
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "git_version": run([GIT, "--version"]).stdout.decode().strip(),
        "gh_version": run([GH, "--version"]).stdout.decode().splitlines()[0].strip(),
        "clone_root": str(pf["clone_root"]),
        "r5_size_interpretation": R5_SIZE_INTERPRETATION,
        "r5_max_git_dir_bytes": R5_MAX_GIT_BYTES,
    }
    log(f"regex digest OK: {_REGEX_DIGEST}")
    for k, v in run_info.items():
        log(f"run {k}: {v}")
    pf["clone_root"].mkdir(parents=False, exist_ok=False)

    log("pool construction (1.3)")
    queries = {b: {t: search_topic(t, log) for t in BUCKET_TOPICS[b]} for b in BUCKET_ORDER}
    pool = build_buckets(queries, log)

    log("walk (1.3): R1 -> R4 -> R2 -> R5")
    walk, selected, owners = {}, [], set()
    for b in BUCKET_ORDER:
        log(f"bucket {b}")
        attempts, sel = walk_bucket(b, pool["buckets"][b]["shuffled_order"], pool["repo_info"],
                                    owners, pf["clone_root"], log)
        walk[b] = {"attempts": attempts, "selected": sel["full_name"] if sel else None}
        if sel:
            selected.append(sel)
            owners.add(sel["full_name"].split("/", 1)[0].lower())

    snapshot = {"run": run_info, "pool": {"queries": queries, **pool}, "walk": walk,
                "unfilled_buckets": [b for b in BUCKET_ORDER if walk[b]["selected"] is None]}
    n = len(selected)
    written = [OUT_LOG]
    if n in ALLOC:
        log(f"sampling (3) with {n} repos")
        strata = {}
        for idx, sel in enumerate(selected):
            strata[sel["full_name"]] = sample_repo(sel, idx, n, log)
        qual_files = {}
        for s in selected:
            qpath = qualifying_path(s["full_name"])
            write_text(qpath, json.dumps(scrub_obj({"full_name": s["full_name"], "head_sha": s["head_sha"],
                                                    "preregistration_commit": pf["prereg_commit"],
                                                    "qualifying_commits": s["history"]["qualifying"]}),
                                         indent=2, ensure_ascii=False) + "\n")
            written.append(qpath)
            qsha = hashlib.sha256(qpath.read_bytes()).hexdigest()
            qual_files[s["full_name"]] = {"path": qpath.relative_to(REPO_ROOT).as_posix(), "sha256": qsha}
            log(f"wrote {qpath} ({len(s['history']['qualifying'])} qualifying commits) sha256={qsha}")
        snapshot["selected"] = [{
            "bucket": s["bucket"], "full_name": s["full_name"], "head_sha": s["head_sha"],
            "spdx_id": s["spdx_id"], "clone_path": s["clone_path"], "R2": s["R2"], "R5": s["R5"],
            "history_stats": s["history"]["stats"], "bot_author_names": s["history"]["bot_author_names"],
            "generated_exclusions": s["history"]["generated_exclusions"],
            "qualifying_commits_count": len(s["history"]["qualifying"]),
            "qualifying_d_only_count": sum(c["d_only"] for c in s["history"]["qualifying"]),
            "qualifying_file": qual_files[s["full_name"]]} for s in selected]
        snapshot["strata"] = strata
        for name in ("random", "keyword", "reserve"):
            taken = [sha for s in selected for sha in strata[s["full_name"]][name]["taken"]]
            d_share = sum(strata[s["full_name"]][name]["d_only_taken"] for s in selected) / len(taken) if taken else None
            log(f"stratum {name}: total={len(taken)} d_only_share={d_share}")
    else:
        log(f"only {n} repo(s) selected; the prereg needs 2 or 3. No sample is drawn.")
    snapshot["run"]["finished_utc"] = utcnow()

    write_text(OUT_SNAPSHOT, json.dumps(scrub_obj(snapshot), indent=2, ensure_ascii=False) + "\n")
    written.append(OUT_SNAPSHOT)
    log(f"wrote {OUT_SNAPSHOT} sha256={hashlib.sha256(OUT_SNAPSHOT.read_bytes()).hexdigest()}")
    if n in ALLOC:
        for name, path in OUT_LISTS.items():
            lines = [f"{s['full_name']} {sha}" for s in selected
                     for sha in snapshot["strata"][s["full_name"]][name]["taken"]]
            write_text(path, "".join(line + "\n" for line in lines))
            written.append(path)
            log(f"wrote {path} ({len(lines)} SHAs) sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")
        log(f"clones kept under {pf['clone_root']} (not deleted by this script)")
    if not self_check(written, log):
        log("RUN FAILED: privacy self-check failed; outputs are NOT finished")
        return 1
    log("self-check passed; run finished")
    return 0 if n in ALLOC else 2


def self_check(paths: list, log: Log) -> bool:
    """Fail if any output contains `Users\\`, or the home directory name as a path
    segment (`Users\\NAME`, `Users/NAME`, `\\NAME\\`, JSON `\\\\NAME\\\\`); case-insensitive.
    NAME next to a lone `/` is not matched, so `owner/name` repo strings pass."""
    checks = [("the string Users\\", lambda text: "users\\" in text.lower())]
    if HOME_NAME:
        name = re.escape(HOME_NAME)
        checks.append(("a Users path segment followed by the home directory name",
                       re.compile(r"users[\\/]+" + name, re.IGNORECASE).search))
        checks.append(("the home directory name between backslashes",
                       re.compile(r"\\+" + name + r"\\+", re.IGNORECASE).search))
    ok = True
    log._f.flush()
    for p in paths:
        text = p.read_text(encoding="utf-8")
        for label, found in checks:
            if found(text):
                log(f"self-check FAILED: {p.relative_to(REPO_ROOT).as_posix()} contains {label}")
                ok = False
    return ok


def _run_entry() -> int:
    """Top-level handler around main(). A str SystemExit is printed scrubbed (exit 1);
    any other exception gets a scrubbed traceback on stderr and in this run's log (exit 3)."""
    try:
        return main()
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(scrub(exc.code), file=sys.stderr, flush=True)
            return 1
        raise
    except BaseException as exc:
        tb = scrub(traceback.format_exc())
        if not tb.endswith("\n"):
            tb += "\n"
        if isinstance(exc, KeyboardInterrupt):
            header = "INTERRUPTED: KeyboardInterrupt; scrubbed traceback follows"
        else:
            header = f"CRASH: unhandled {type(exc).__name__}; scrubbed traceback follows"
        if _LOG_OPENED:
            try:
                with open(OUT_LOG, "a", encoding="utf-8", newline="\n") as f:
                    f.write(f"{utcnow()} {header}\n")
                    f.write(tb)
            except OSError as log_exc:
                print(scrub(f"could not append traceback to the log: {log_exc!r}"),
                      file=sys.stderr, flush=True)
        print(tb, end="", file=sys.stderr, flush=True)
        return 3


if __name__ == "__main__":
    sys.exit(_run_entry())
