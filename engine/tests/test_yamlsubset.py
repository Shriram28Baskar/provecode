"""provecode.yamlsubset (ADR 0012 section 3): matches PyYAML's BaseLoader on the subset, and refuses
everything else instead of guessing."""

import pytest
import yaml

from provecode.yamlsubset import YamlError, YamlUnsupported, loads

SAME = [
    "on:\n  push:\n    branches: [main, 'release/*']\n  pull_request:\n",
    "jobs:\n  t:\n    steps:\n    - uses: a/b@v1\n      with:\n        x: 1\n    - run: echo hi\n",
    "a: |\n  line 1\n    indented\n  line 3\nb: |-\n  stripped\nc: |+\n  kept\n\nd: >\n  folded\n  text\n\n  para\n",
    "e: >-\n  one\n   more\n  two\n",
    "k: \"esc \\t \\u00e9 \\\\ \\\"q\\\"\"\nl: 'it''s'\nm: plain # comment\n",
    "n: \"multi\n  line\n\n  quoted\"\no: plain\n  continued\n  here\n",
    "p: {a: 1, b: [x, y], c: {d: e}}\nq: [1, [2, 3], {k: v}]\nr: []\ns: {}\n",
    "t: &anchor\n  x: 1\nu: *anchor\nv: &s scalar\nw: *s\n",
    "---\nx: 1\n",
    "- a\n- - b\n  - c\n- d: e\n  f: g\n",
    "env:\n  URL: https://example.com:8080/path\n  EXPR: ${{ matrix.os == 'ubuntu' && 'yes' || 'no' }}\n",
    "on: [push, pull_request]\nname:\nempty_value:\n",
    "key:\n- one\n- two\nnext: 3\n",
    "a: |2\n    two extra\n   one extra\n",
    "x: \"folded \\\n  escaped break\"\n",
]


@pytest.mark.parametrize("text", SAME)
def test_matches_pyyaml_baseloader(text):
    assert loads(text) == yaml.load(text, Loader=yaml.BaseLoader)


@pytest.mark.parametrize("text", [
    "a: !!str 1\n",
    "a: &x {b: 1}\nc:\n  <<: *x\n",
    "? complex\n: key\n",
    "a: 1\n---\nb: 2\n",
    "%YAML 1.2\n---\na: 1\n",
])
def test_refuses_outside_the_subset(text):
    with pytest.raises(YamlUnsupported):
        loads(text)


@pytest.mark.parametrize("text", [
    "a: b: c\n",
    "a: \"unterminated\n",
    "a: *missing\n",
    "a:\n\t- tab\n",
    "a: [1, 2\n",
])
def test_rejects_malformed_input(text):
    with pytest.raises(YamlError):
        loads(text)


def test_deep_nesting_is_refused_not_a_crash():
    text = "a: " + "[" * 500 + "]" * 500 + "\n"
    with pytest.raises(YamlError):
        loads(text)


def test_scalars_stay_strings():
    assert loads("on: true\nn: 3\nf: 1.5\nnull_: null\n") == {"on": "true", "n": "3", "f": "1.5", "null_": "null"}
