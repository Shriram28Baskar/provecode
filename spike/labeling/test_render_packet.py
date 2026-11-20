"""Unit tests for render_packet.py.

Standard library unittest only. Every test uses a throwaway git repository in
a temporary folder, with local-only git config. No real repo, no network, no
clones, and no files under spike/ other than the script under test are used.

Run: python -I -B spike/labeling/test_render_packet.py
"""

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "render_packet.py"
THIS_FILE = Path(__file__).resolve()

_spec = importlib.util.spec_from_file_location("render_packet", SCRIPT)
render_packet = importlib.util.module_from_spec(_spec)
sys.modules["render_packet"] = render_packet
_spec.loader.exec_module(render_packet)
rp = render_packet

# Special values built without backslash escapes.
CR = chr(13)
LF = chr(10)
NUL = chr(0)
BOM_BYTES = bytes.fromhex("efbbbf")
REPLACEMENT_BYTES = bytes.fromhex("efbfbd")

REPO = "acme/widgets"
CLONE_DIRNAME = "acme__widgets"
ISOLATED_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
NON_ASCII_SUBJECT = "Relâche l'assertion — naïve 测试"
NON_ASCII_BODY = "Corps du message: ünïcödé ✓"
MACOS_PATH = "/Users/someone/project"


def git(cwd, *args, raw=False):
    cmd = [
        "git",
        "-c", "user.name=Test Author",
        "-c", "user.email=test@example.invalid",
        "-c", "core.autocrlf=false",
        "-c", "init.defaultBranch=main",
        "-c", "commit.gpgsign=false",
        *args,
    ]
    result = subprocess.run(
        cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args[0]} failed: {result.stderr.decode('utf-8', errors='replace')}")
    if raw:
        return result.stdout
    return result.stdout.decode("utf-8", errors="replace").strip()


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def section_d(text):
    start = rp.SEPARATOR + "\nD. TEST DIFF\n"
    end = "\n" + rp.SEPARATOR + "\nEND OF PACKET\n"
    return text.split(start, 1)[1].split(end, 1)[0]


def section_c(text):
    start = rp.SEPARATOR + "\nC. OTHER CHANGED FILES\n"
    end = "\n" + rp.SEPARATOR + "\nD. TEST DIFF\n"
    return text.split(start, 1)[1].split(end, 1)[0]


def header_value(text, key):
    for line in text.split("\n"):
        if line.startswith(key + ": "):
            return line[len(key) + 2:]
    raise AssertionError(f"header line {key!r} not found")


class PacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env_patch = mock.patch.dict(os.environ, ISOLATED_ENV)
        env_patch.start()
        cls.addClassCleanup(env_patch.stop)
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        base = Path(cls._tmp.name)
        cls.clone_root = base / "clones"
        cls.clone = cls.clone_root / CLONE_DIRNAME
        cls.clone.mkdir(parents=True)
        msg_file = base / "message.txt"
        c = cls.clone

        def write(rel, content):
            path = c / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))

        def commit(message):
            msg_file.write_bytes(message.encode("utf-8"))
            git(c, "add", "-A")
            git(c, "commit", "-q", "-F", str(msg_file))
            return git(c, "rev-parse", "HEAD")

        git(c, "init", "-q")
        write("tests/test_mod.py",
              "def test_total():\n    total = 80\n    assert total == 80\n\n\n"
              "def test_other():\n    assert 1 + 1 == 2\n")
        write("tests/test_del.py", "def test_gone():\n    assert True\n")
        old_lines = "".join(f"def test_case_{i}():\n    assert {i} == {i}\n\n\n" for i in range(10))
        write("tests/test_old.py", old_lines)
        write("tests/test_crlf.py", "def test_crlf():" + CR + LF + "    assert 1 == 1" + CR + LF)
        write("tests/test_paths.py", "def test_path():\n    assert True\n")
        write("src/app.py", "VALUE = 1\n")
        cls.c0 = commit("base\n")

        write("tests/test_mod.py",
              "def test_total():\n    total = 80\n    assert total >= 0\n\n\n"
              "def test_other():\n    assert 1 + 1 == 2\n")
        write("src/app.py", "VALUE = 2\n")
        write("src/new_module.py", "NEW = True\n")
        cls.c_mod = commit(f"{NON_ASCII_SUBJECT}\n\n{NON_ASCII_BODY}\n")

        git(c, "rm", "-q", "tests/test_del.py")
        cls.c_del = commit("remove a test file\n")

        git(c, "mv", "tests/test_old.py", "tests/test_new.py")
        write("tests/test_new.py", old_lines.replace("assert 3 == 3", "assert 3 is not None"))
        cls.c_ren = commit("rename and modify\n")

        write("tests/test_crlf.py",
              "def test_crlf():" + CR + LF + "    assert 1 == 1" + CR + LF + "    assert 2 == 2" + CR + LF)
        cls.c_crlf = commit("crlf change\n")

        write("tests/test_paths.py", f'HOME = "{MACOS_PATH}"\n\n\ndef test_path():\n    assert HOME\n')
        cls.c_paths = commit("use a fixture path\n")

        write("tests/test_mod.py",
              "def test_total():\n    total = 80\n    assert total >= 0\n\n\n"
              "def test_other():\n    assert 1 + 1 >= 0\n")
        cls.c_leak = commit(f"built in {cls.clone_root}\n")

        write("tests/test_mod.py",
              "def test_total():\n    total = 80\n    assert total >= 0\n\n\n"
              "def test_other():\n    assert True\n")
        cls.c_unlisted = commit("not in any list\n")
        cls.head = git(c, "rev-parse", "HEAD")

        tree = git(c, "diff-tree", "-r", "--no-commit-id", "-M50%", "--name-status", cls.c_del, cls.c_ren)
        status = tree.split("\t", 1)[0]
        assert status.startswith("R"), tree
        cls.rename_similarity = int(status[1:])

        def m(path):
            return [{"status": "M", "old": path, "new": path, "similarity": None}]

        cls.files = {
            cls.c_mod: m("tests/test_mod.py"),
            cls.c_del: [{"status": "D", "old": "tests/test_del.py", "new": None, "similarity": None}],
            cls.c_ren: [{"status": "R", "old": "tests/test_old.py", "new": "tests/test_new.py",
                         "similarity": cls.rename_similarity}],
            cls.c_crlf: m("tests/test_crlf.py"),
            cls.c_paths: m("tests/test_paths.py"),
            cls.c_leak: m("tests/test_mod.py"),
            cls.c_unlisted: m("tests/test_mod.py"),
        }
        cls.parents = {sha: git(c, "rev-parse", f"{sha}^") for sha in cls.files}
        cls.random_shas = [cls.c_mod, cls.c_del, cls.c_ren, cls.c_leak]
        cls.keyword_shas = [cls.c_crlf, cls.c_paths]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "root"
        self.packets = self.root / "spike" / "packets"
        self.config = self.make_config()

    # ------------------------------------------------------------ helpers

    def make_config(self, head_sha=None, parents=None):
        parents = parents or {}
        spike = self.root / "spike"
        spike.mkdir(parents=True, exist_ok=True)
        random_path = spike / "random.txt"
        keyword_path = spike / "keyword.txt"
        json_path = spike / f"qualifying_{CLONE_DIRNAME}.json"
        random_path.write_bytes("".join(f"{REPO} {s}\n" for s in self.random_shas).encode("utf-8"))
        keyword_path.write_bytes("".join(f"{REPO} {s}\n" for s in self.keyword_shas).encode("utf-8"))
        doc = {
            "full_name": REPO,
            "head_sha": head_sha or self.head,
            "preregistration_commit": "0" * 40,
            "qualifying_commits": [
                {
                    "sha": sha,
                    "parent": parents.get(sha, self.parents[sha]),
                    "d_only": all(f["status"] == "D" for f in files),
                    "files": files,
                }
                for sha, files in self.files.items()
            ],
        }
        json_path.write_bytes(json.dumps(doc, indent=2).encode("utf-8"))
        return rp.Config(
            repo_root=self.root,
            random_list=rp.InputFile(random_path, sha256_of(random_path)),
            keyword_list=rp.InputFile(keyword_path, sha256_of(keyword_path)),
            qualifying={REPO: rp.InputFile(json_path, sha256_of(json_path))},
        )

    def run_main(self, *argv, config=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rp.main(["--clone-root", str(self.clone_root), *argv], config=config or self.config)
        return code, out.getvalue(), err.getvalue()

    def packet_path(self, sha, out_dir=None):
        return (out_dir or self.packets) / CLONE_DIRNAME / f"{sha}.txt"

    def render_ok(self, sha):
        code, out, err = self.run_main("--sha", sha)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "rendered 1, skipped 0, failed 0\n")
        data = self.packet_path(sha).read_bytes()
        self.assertNotIn(CR.encode("ascii"), data)
        return data.decode("utf-8")

    def assert_nothing_written(self):
        self.assertFalse(self.packets.exists(), "packets folder was created")

    # ------------------------------------------------------------ packet content

    def test_modified_file(self):
        text = self.render_ok(self.c_mod)
        self.assertTrue(text.startswith("PACKET\nrepo: acme/widgets\n"))
        self.assertEqual(header_value(text, "sha"), self.c_mod)
        self.assertEqual(header_value(text, "stratum"), "random")
        self.assertEqual(header_value(text, "parent"), self.parents[self.c_mod])
        self.assertEqual(header_value(text, "qualifying files"), "1")
        self.assertEqual(header_value(text, "CR characters normalised in test diff"), "0")
        self.assertIn("\nM\ttests/test_mod.py\ttests/test_mod.py\t-\n", text)
        self.assertIn("Units are NOT listed here. List them by hand from the diff below.\n", text)
        diff = section_d(text)
        self.assertIn("-    assert total == 80", diff)
        self.assertIn("+    assert total >= 0", diff)
        self.assertIn(" def test_total():", diff)  # function context from -W
        self.assertEqual(int(header_value(text, "diff lines")), diff.count("\n") + 1)
        self.assertTrue(text.endswith(rp.SEPARATOR + "\nEND OF PACKET\n"))
        self.assertNotIn("Test Author", text)
        self.assertNotIn("example.invalid", text)
        self.assertNotIn("Date:", text)

    def test_deleted_file(self):
        text = self.render_ok(self.c_del)
        self.assertIn("\nD\ttests/test_del.py\t-\t-\n", text)
        diff = section_d(text)
        self.assertIn("deleted file mode", diff)
        self.assertIn("-    assert True", diff)
        self.assertEqual(section_c(text), "# all changed files that are not qualifying test files, with git status\n(none)")
        self.assertEqual(header_value(text, "CR characters normalised in test diff"), "0")

    def test_renamed_and_modified_file(self):
        text = self.render_ok(self.c_ren)
        self.assertIn(f"\nR\ttests/test_old.py\ttests/test_new.py\t{self.rename_similarity}\n", text)
        diff = section_d(text)
        self.assertIn("rename from tests/test_old.py", diff)
        self.assertIn("rename to tests/test_new.py", diff)
        self.assertIn("-    assert 3 == 3", diff)
        self.assertIn("+    assert 3 is not None", diff)
        self.assertIn("(none)", section_c(text))
        self.assertEqual(header_value(text, "CR characters normalised in test diff"), "0")

    def test_added_non_test_file_in_section_c(self):
        text = self.render_ok(self.c_mod)
        lines = section_c(text).split("\n")
        self.assertEqual(lines[0], "# all changed files that are not qualifying test files, with git status")
        self.assertIn("A\tsrc/new_module.py", lines)
        self.assertIn("M\tsrc/app.py", lines)
        self.assertFalse(any("tests/test_mod.py" in line for line in lines))

    def test_non_ascii_commit_message(self):
        text = self.render_ok(self.c_mod)
        expected = (rp.SEPARATOR + "\nB. COMMIT MESSAGE\n" + NON_ASCII_SUBJECT + "\n\n"
                    + NON_ASCII_BODY + "\n" + rp.SEPARATOR + "\n")
        self.assertIn(expected, text)

    def test_crlf_test_file(self):
        blob = git(self.clone, "show", f"{self.c_crlf}:tests/test_crlf.py", raw=True)
        self.assertIn((CR + LF).encode("ascii"), blob, "fixture must really contain CRLF")
        text = self.render_ok(self.c_crlf)  # asserts no CR byte in the file
        self.assertNotIn(CR, text)
        self.assertGreater(int(header_value(text, "CR characters normalised in test diff")), 0)
        self.assertEqual(header_value(text, "stratum"), "keyword")
        self.assertIn("+    assert 2 == 2\n", section_d(text) + "\n")

    def test_macos_users_path_in_diff_renders(self):
        text = self.render_ok(self.c_paths)
        self.assertIn(MACOS_PATH, section_d(text))
        self.assertEqual(header_value(text, "CR characters normalised in test diff"), "0")

    def test_hostile_global_config_does_not_change_packet(self):
        home = self.base / "home"
        home.mkdir()
        (home / ".gitconfig").write_bytes(b"[diff]\n\tnoprefix = true\n\talgorithm = histogram\n")
        hostile = {"HOME": str(home), "USERPROFILE": str(home)}
        with mock.patch.dict(os.environ, hostile):
            for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "XDG_CONFIG_HOME"):
                os.environ.pop(key, None)
            control = git(self.clone, "show", "--format=", self.c_mod, "--", "tests/test_mod.py")
            self.assertNotIn("--- a/", control, "hostile config was not picked up; the test proves nothing")
            text = self.render_ok(self.c_mod)
        diff = section_d(text)
        self.assertIn("diff --git a/tests/test_mod.py b/tests/test_mod.py", diff)
        self.assertIn("--- a/tests/test_mod.py", diff)
        self.assertIn("+++ b/tests/test_mod.py", diff)

    # ------------------------------------------------------------ guards

    def test_sha_not_in_lists_generic_message(self):
        for sha in ("0" * 40, self.c_unlisted, "not-a-sha", self.c_mod.upper()):
            with self.subTest(sha=sha):
                code, out, err = self.run_main("--sha", sha)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(err, "SHA is not in the pre-registered random or keyword lists.\n")
                self.assert_nothing_written()

    def test_tampered_input_hash_aborts(self):
        for item in (self.config.random_list, self.config.keyword_list, self.config.qualifying[REPO]):
            with self.subTest(file=item.path.name):
                original = item.path.read_bytes()
                item.path.write_bytes(original + b"\n")
                try:
                    code, out, err = self.run_main("--all")
                finally:
                    item.path.write_bytes(original)
                self.assertEqual(code, 2)
                self.assertIn("SHA-256 mismatch", err)
                self.assertIn("Nothing was written.", err)
                self.assert_nothing_written()

    def test_head_mismatch_aborts(self):
        config = self.make_config(head_sha=self.c_leak)
        code, out, err = self.run_main("--sha", self.c_mod, config=config)
        self.assertEqual(code, 2)
        self.assertIn("HEAD", err)
        self.assert_nothing_written()

    def test_parent_mismatch_aborts(self):
        config = self.make_config(parents={self.c_mod: self.c_del})
        code, out, err = self.run_main("--sha", self.c_mod, config=config)
        self.assertEqual(code, 2)
        self.assertIn("parent", err)
        self.assert_nothing_written()

    def test_out_dir_inside_repo_but_outside_packets_aborts(self):
        for target in (self.root / "spike" / "other", self.root / "docs", self.root):
            with self.subTest(target=target.name):
                code, out, err = self.run_main("--sha", self.c_mod, "--out-dir", str(target))
                self.assertEqual(code, 2)
                self.assertIn("spike/packets", err)
                self.assertFalse((target / CLONE_DIRNAME).exists())
                self.assert_nothing_written()

    def test_out_dir_outside_repo_is_allowed(self):
        elsewhere = self.base / "elsewhere"
        code, out, err = self.run_main("--sha", self.c_mod, "--out-dir", str(elsewhere))
        self.assertEqual(code, 0, err)
        self.assertTrue(self.packet_path(self.c_mod, elsewhere).is_file())

    def test_leak_guard_refuses_packet_and_continues(self):
        code, out, err = self.run_main("--sha", self.c_leak)
        self.assertEqual(code, 1)
        self.assertEqual(out, f"rendered 0, skipped 0, failed 1\nfailed: {self.c_leak}\n")
        self.assertIn("clone-root path", err)
        self.assertNotIn(str(self.clone_root), err + out)
        folder = self.packets / CLONE_DIRNAME
        self.assertEqual(list(folder.iterdir()) if folder.exists() else [], [])

        code, out, err = self.run_main("--all")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("rendered 5, skipped 0, failed 1\n"))
        self.assertFalse(self.packet_path(self.c_leak).exists())
        for sha in self.random_shas + self.keyword_shas:
            if sha != self.c_leak:
                self.assertTrue(self.packet_path(sha).is_file())

    def test_leak_needles_case_and_slash_forms(self):
        needles = rp.leak_needles(str(self.clone_root))
        resolved = str(self.clone_root.resolve())
        self.assertIn(resolved.replace("\\", "/").casefold(), needles)
        self.assertTrue(rp.contains_leak("x " + resolved.replace("\\", "/").upper() + " y", needles))
        self.assertFalse(rp.contains_leak("path " + MACOS_PATH, needles))

    def test_identical_rerun_is_skipped(self):
        first = self.render_ok(self.c_mod)
        code, out, err = self.run_main("--sha", self.c_mod)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "rendered 0, skipped 1, failed 0\n")
        self.assertEqual(self.packet_path(self.c_mod).read_bytes().decode("utf-8"), first)

    def test_differing_existing_file_fails_without_overwrite(self):
        self.render_ok(self.c_mod)
        path = self.packet_path(self.c_mod)
        tampered = path.read_bytes() + b"tampered\n"
        path.write_bytes(tampered)
        code, out, err = self.run_main("--sha", self.c_mod)
        self.assertEqual(code, 1)
        self.assertEqual(out, f"rendered 0, skipped 0, failed 1\nfailed: {self.c_mod}\n")
        self.assertIn(self.c_mod, err)
        self.assertEqual(path.read_bytes(), tampered)

    def test_stratum_filter(self):
        code, out, err = self.run_main("--all", "--stratum", "keyword")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "rendered 2, skipped 0, failed 0\n")
        written = sorted(p.stem for p in (self.packets / CLONE_DIRNAME).iterdir())
        self.assertEqual(written, sorted(self.keyword_shas))

    def test_argument_rules(self):
        cases = [
            ["--sha", "0" * 40, "--stratum", "random"],
            ["--all", "--stratum", "other"],
            ["--sha", "0" * 40, "--all"],
            [],
        ]
        for extra in cases:
            with self.subTest(argv=extra):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                    rp.main(["--clone-root", str(self.clone_root), *extra], config=self.config)
                self.assertEqual(ctx.exception.code, 2)
        self.assert_nothing_written()

    # ------------------------------------------------------------ BOM / normalise

    def test_bom_constant_and_normalise(self):
        self.assertEqual(len(render_packet.BOM), 1)
        self.assertEqual(ord(render_packet.BOM), 0xFEFF)
        text, count, lone = rp.normalise(BOM_BYTES + b"abc")
        self.assertEqual(text, "abc")
        self.assertEqual((count, lone), (0, False))
        text, _, _ = rp.normalise(REPLACEMENT_BYTES + b"abc")
        self.assertEqual(text, chr(0xFFFD) + "abc")
        text, count, lone = rp.normalise(("a" + CR + LF + "b" + CR + "c").encode("ascii"))
        self.assertEqual(text, "a" + LF + "b" + LF + "c")
        self.assertEqual((count, lone), (2, True))

    def test_parse_list_strips_bom(self):
        data = BOM_BYTES + f"{REPO} {self.c_mod}\n".encode("utf-8")
        entries = rp.parse_list(data, "random", "test list", self.config)
        self.assertEqual(entries, [rp.Listed(REPO, self.c_mod, "random")])

    def test_name_status_parser(self):
        tokens = ["M", "a.py", "R087", "old.py", "new.py", "A", "b.py", ""]
        entries = rp.parse_name_status_z(NUL.join(tokens))
        self.assertEqual(entries, [("M", ["a.py"]), ("R087", ["old.py", "new.py"]), ("A", ["b.py"])])


class StaticChecks(unittest.TestCase):
    def test_no_bom_or_replacement_bytes_in_sources(self):
        for path in (SCRIPT, THIS_FILE):
            with self.subTest(file=path.name):
                data = path.read_bytes()
                self.assertNotIn(BOM_BYTES, data)
                self.assertNotIn(REPLACEMENT_BYTES, data)

    def test_imports_are_stdlib_only(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
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

    def test_source_has_no_forbidden_strings(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("reserve", source.lower())
        self.assertNotIn("pool_snapshot", source)

    def test_cli_help_runs_isolated(self):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), "--help"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"--clone-root", result.stdout)


if __name__ == "__main__":
    unittest.main()
