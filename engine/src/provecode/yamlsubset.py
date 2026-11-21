"""A small, fail-closed YAML subset parser for GitHub workflow files (ADR 0012, section 3). Stdlib only.

Supported: block mappings and sequences (including compact `- key: value` items and sequences at
their parent key's indentation), plain, single- and double-quoted scalars (also multi-line),
literal and folded block scalars with chomping and indentation indicators, flow sequences and
mappings, comments, a leading `---`, anchors and aliases.

Every scalar is returned as a string, exactly as PyYAML's BaseLoader does, so `on:` stays the key
"on" and `true` stays "true". Anything outside the subset (tags, merge keys, complex keys, several
documents, directives) raises YamlUnsupported; malformed input raises YamlError. Callers turn
both into an `unparseable` gap and never guess.
"""

from __future__ import annotations

import copy
import re
from typing import Any

Node = Any  # a parsed YAML node: dict[str, Node], list[Node], str or None

MAX_DEPTH = 64


class YamlError(ValueError):
    """Malformed input."""


class YamlUnsupported(YamlError):
    """Valid YAML, perhaps, but outside the subset this parser accepts."""


_ANCHOR = (r"^&([^\s\[\]{},]+)\s*")
_ALIAS = (r"^\*([^\s\[\]{},]+)\s*$")
_ESC = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
        "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": "\u2028",
        "P": "\u2029"}


def _strip_comment(text: str) -> str:
    """Remove a trailing ` # comment` outside quotes."""
    q = None
    i = 0
    while i < len(text):
        c = text[i]
        if q:
            if q == '"' and c == "\\":
                i += 2
                continue
            if c == q:
                if q == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                q = None
        elif c in "'\"" and (i == 0 or text[i - 1] in " \t[{,:"):
            q = c
        elif c == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def _plain_key(c: str) -> tuple[str, str] | None:
    """A plain mapping key: the text up to the first `:` followed by a space or the line end."""
    if not c or c[0] in "[]{}&*!|>'\"%@`#,":
        return None
    if c.startswith(("- ", "? ")) or c in ("-", "?"):
        return None
    j = 0
    while True:
        k = c.find(":", j)
        if k < 0:
            return None
        if k + 1 == len(c) or c[k + 1] in " \t":
            key = c[:k]
            if " #" in key or "\t#" in key:
                return None
            return key.rstrip(), c[k + 1:].lstrip(" \t")
        j = k + 1


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blank(line: str) -> bool:
    s = line.strip()
    return not s or s.startswith("#")


class _Parser:
    def __init__(self, text: str):
        if text.startswith("\ufeff"):
            text = text[1:]
        for n, line in enumerate(text.split("\n"), start=1):
            lead = line[:len(line) - len(line.lstrip(" \t"))]
            if "\t" in lead and not _blank(line):
                raise YamlError(f"line {n}: tab used for indentation")
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.i = 0
        self.anchors: dict[str, Node] = {}
        self.depth = 0

    # ------------------------------------------------------------------ helpers

    def _skip(self) -> None:
        while self.i < len(self.lines) and _blank(self.lines[self.i]):
            self.i += 1

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise YamlUnsupported(f"nesting deeper than {MAX_DEPTH}")

    # ------------------------------------------------------------------ document

    def document(self) -> Node:
        self._skip()
        if self.i < len(self.lines) and self.lines[self.i].startswith("%"):
            raise YamlUnsupported("directives are not supported")
        if self.i < len(self.lines) and self.lines[self.i].rstrip() in ("---",) :
            self.i += 1
            self._skip()
        elif self.i < len(self.lines) and self.lines[self.i].startswith("--- "):
            raise YamlUnsupported("content on the document start line")
        if self.i >= len(self.lines):
            return None
        col = _indent(self.lines[self.i])
        node = self.block(col)
        self._skip()
        if self.i < len(self.lines):
            rest = self.lines[self.i].rstrip()
            if rest in ("---", "...") or rest.startswith("--- "):
                self.i += 1
                self._skip()
                if self.i < len(self.lines):
                    raise YamlUnsupported("more than one document")
            else:
                raise YamlError(f"line {self.i + 1}: unexpected content")
        return node

    # ------------------------------------------------------------------ block structures

    def block(self, col: int) -> Node:
        self._enter()
        try:
            line = self.lines[self.i]
            content = line[col:]
            if content.startswith("- ") or content.rstrip() == "-":
                return self.sequence(col)
            if self._key(content) is not None:
                return self.mapping(col)
            # A bare scalar, flow collection, alias or anchored node standing alone.
            self.i += 1
            return self.value(_strip_comment(content.strip()) if not content.lstrip().startswith(("'", '"'))
                              else content.strip(), col - 1, first_line=self.i - 1)
        finally:
            self.depth -= 1

    def _key(self, content: str) -> tuple[str, str] | None:
        """(key, rest of line) when content starts a mapping entry, else None."""
        c = content
        if c.startswith("? ") or c.rstrip() == "?":
            raise YamlUnsupported("complex mapping keys are not supported")
        if c[:1] in ("'", '"'):
            end = self._quoted_end(c, 0)
            if end is None:
                return None
            after = c[end + 1:]
            m = re.match(r"^\s*:(?:\s+|$)", after)
            if not m:
                return None
            return self._unquote(c[:end + 1]), after[m.end():]
        if c[:1] in ("&", "*", "!"):
            if re.match(r"^[&*!]\S*\s+\S.*?:(?:\s|$)", c) and not c.startswith("*"):
                raise YamlUnsupported("anchors, aliases or tags on mapping keys are not supported")
            return None
        kv = _plain_key(c)
        if kv is not None and kv[0] == "<<":
            raise YamlUnsupported("merge keys are not supported")
        return kv

    def mapping(self, col: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while True:
            self._skip()
            if self.i >= len(self.lines):
                return out
            line = self.lines[self.i]
            ind = _indent(line)
            if ind < col:
                return out
            if ind > col:
                raise YamlError(f"line {self.i + 1}: unexpected indentation")
            content = line[col:]
            if content.startswith("- ") or content.rstrip() == "-":
                return out  # a sequence at a parent key's column ends this mapping
            kv = self._key(content)
            if kv is None:
                if content.rstrip() in ("---", "..."):
                    return out
                raise YamlError(f"line {self.i + 1}: expected a mapping key")
            key, rest = kv
            first = self.i
            self.i += 1
            out[key] = self.value(rest, col, first_line=first, in_mapping=True)

    def sequence(self, col: int) -> list[Any]:
        out: list[Any] = []
        while True:
            self._skip()
            if self.i >= len(self.lines):
                return out
            line = self.lines[self.i]
            ind = _indent(line)
            if ind < col:
                return out
            content = line[col:]
            if ind > col or not (content.startswith("- ") or content.rstrip() == "-"):
                if ind == col:
                    return out
                raise YamlError(f"line {self.i + 1}: unexpected indentation in a sequence")
            rest = content[1:]
            stripped = rest.lstrip(" ")
            if not stripped or stripped.startswith("#"):
                self.i += 1
                out.append(self._nested_or_empty(col, allow_same=False))
                continue
            inner = col + 1 + (len(rest) - len(stripped))
            if (stripped.startswith("- ") or stripped.rstrip() == "-" or
                    (self._key(stripped) is not None and not stripped.startswith(("&", "*", "!")))):
                # Compact nested collection: re-read this line as if it began at `inner`.
                self.lines[self.i] = " " * inner + stripped
                self._enter()
                try:
                    out.append(self.block(inner))
                finally:
                    self.depth -= 1
                continue
            first = self.i
            self.i += 1
            out.append(self.value(stripped, col, first_line=first))

    def _nested_or_empty(self, col: int, allow_same: bool) -> Node:
        """The node below a `key:` or `-` with nothing after it."""
        self._skip()
        if self.i >= len(self.lines):
            return ""
        line = self.lines[self.i]
        ind = _indent(line)
        if ind > col:
            return self.block(ind)
        if allow_same and ind == col and (line[col:].startswith("- ") or line[col:].rstrip() == "-"):
            return self.sequence(col)
        return ""

    # ------------------------------------------------------------------ values

    def value(self, rest: str, col: int, *, first_line: int, in_mapping: bool = False) -> Node:
        """Parse the value text `rest` that follows a key or dash on line first_line. self.i points
        after that line. col is the column of the owning key (or dash)."""
        anchor = None
        m = re.match(_ANCHOR, rest)
        if m:
            anchor = m.group(1)
            rest = rest[m.end():]
        if rest.startswith("!"):
            raise YamlUnsupported("tags are not supported")
        stripped = _strip_comment(rest) if rest[:1] not in ("'", '"') else rest
        if not stripped or stripped.startswith("#"):
            node = self._nested_or_empty(col, allow_same=in_mapping)
        elif stripped[0] in "|>":
            node = self.block_scalar(stripped, col)
        elif stripped[0] in "[{":
            node = self.flow_text(stripped, col)
        elif stripped[0] == "*":
            am = re.match(_ALIAS, stripped)
            if not am:
                raise YamlError(f"line {first_line + 1}: bad alias")
            if am.group(1) not in self.anchors:
                raise YamlError(f"line {first_line + 1}: unknown alias *{am.group(1)}")
            node = copy.deepcopy(self.anchors[am.group(1)])
        elif stripped[0] in "'\"":
            node = self.quoted(rest.strip(), col)
        else:
            node = self.plain(stripped, col)
        if anchor is not None:
            self.anchors[anchor] = node
        return node

    def _continuation(self, col: int) -> list[str]:
        """Following raw lines more indented than col (blank lines included), consumed."""
        out = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() == "":
                out.append("")
                self.i += 1
                continue
            if _indent(line) <= col:
                break
            out.append(line)
            self.i += 1
        while out and out[-1] == "":
            out.pop()
            self.i -= 1
        return out

    def plain(self, text: str, col: int) -> str:
        if text[0] in "@`%" or text.startswith(("- ", "? ", ": ")):
            raise YamlError(f"plain scalar cannot start with {text[0]!r}")
        if re.search(r":(\s|$)", text):
            raise YamlError("mapping values are not allowed here")
        parts = [text]
        for line in self._continuation(col):
            s = line.strip()
            if s.startswith("#"):
                raise YamlError("comment inside a multi-line plain scalar")
            parts.append(_strip_comment(s) if s else "")
            if re.search(r":(\s|$)", parts[-1]):
                raise YamlError("mapping values are not allowed here")
        return _fold(parts)

    def _quoted_end(self, text: str, start: int) -> int | None:
        q = text[start]
        i = start + 1
        while i < len(text):
            c = text[i]
            if q == '"' and c == "\\":
                i += 2
                continue
            if c == q:
                if q == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                return i
            i += 1
        return None

    def quoted(self, text: str, col: int) -> str:
        buf = text
        end = self._quoted_end(buf, 0)
        while end is None:
            if self.i >= len(self.lines):
                raise YamlError("unterminated quoted scalar")
            buf += "\n" + self.lines[self.i]
            self.i += 1
            end = self._quoted_end(buf, 0)
        tail = _strip_comment(buf[end + 1:]).strip()
        if tail:
            raise YamlError(f"unexpected text after a quoted scalar: {tail!r}")
        return self._unquote(buf[:end + 1])

    def _unquote(self, raw: str) -> str:
        q, body = raw[0], raw[1:-1]
        lines = body.split("\n")
        if len(lines) > 1:
            # Flow folding (YAML 1.2 section 7.3): a line break becomes a space, each empty line a
            # newline; leading and trailing white space around breaks is dropped. In double quotes a
            # break after an odd number of backslashes is escaped and joins the lines directly.
            parts = [lines[0].rstrip(" \t")] + [x.strip(" \t") for x in lines[1:-1]] + [lines[-1].lstrip(" \t")]
            out, empties = parts[0], 0
            for idx in range(1, len(parts)):
                part = parts[idx]
                if part == "" and idx != len(parts) - 1:
                    empties += 1
                    continue
                sep = "\n" * empties if empties else " "
                if q == '"' and _escaped_break(out):
                    out, sep = out[:-1], "\n" * empties
                out += sep + part
                empties = 0
            body = out
        if q == "'":
            return body.replace("''", "'")
        return _unescape(body)

    def block_scalar(self, header: str, col: int) -> str:
        m = re.match(r"^([|>])([+-]?)([1-9]?)([+-]?)\s*(#.*)?$", header)
        if not m or (m.group(2) and m.group(4)):
            raise YamlError(f"bad block scalar header {header!r}")
        style, chomp, explicit = m.group(1), m.group(2) or m.group(4), m.group(3)
        raw = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() == "" or _indent(line) > col:
                raw.append(line)
                self.i += 1
            else:
                break
        trailing = 0
        while raw and raw[-1].strip() == "":
            raw.pop()
            trailing += 1
        if explicit:
            ind = col + int(explicit) if col >= 0 else int(explicit)
        else:
            ind = next((_indent(line) for line in raw if line.strip()), col + 1)
        body = []
        for line in raw:
            if line.strip() == "":
                body.append(line[ind:] if len(line) > ind else "")
            elif _indent(line) < ind:
                raise YamlError("block scalar line less indented than its content")
            else:
                body.append(line[ind:])
        text = "\n".join(body) if style == "|" else _fold_block(body)
        if not body:
            return "\n" * trailing if chomp == "+" else ""
        if chomp == "-":
            return text
        if chomp == "+":
            return text + "\n" + "\n" * trailing
        return text + "\n"

    # ------------------------------------------------------------------ flow

    def flow_text(self, text: str, col: int) -> Node:
        buf = _strip_comment(text)
        while _unbalanced(buf):
            if self.i >= len(self.lines):
                raise YamlError("unterminated flow collection")
            buf += " " + _strip_comment(self.lines[self.i].strip())
            self.i += 1
        node, pos = _Flow(buf, self).node(0)
        if buf[pos:].strip():
            raise YamlError(f"unexpected text after a flow collection: {buf[pos:].strip()!r}")
        return node


def _unbalanced(text: str) -> bool:
    depth, q, i = 0, None, 0
    while i < len(text):
        c = text[i]
        if q:
            if q == '"' and c == "\\":
                i += 2
                continue
            if c == q:
                if q == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                q = None
        elif c in "'\"":
            q = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        i += 1
    return depth > 0 or q is not None


class _Flow:
    def __init__(self, text: str, parser: _Parser):
        self.t, self.p = text, parser

    def _ws(self, i: int) -> int:
        while i < len(self.t) and self.t[i] in " \t":
            i += 1
        return i

    def node(self, i: int) -> tuple[Node, int]:
        self.p._enter()
        try:
            i = self._ws(i)
            anchor = None
            node: Node
            m = re.match(_ANCHOR, self.t[i:])
            if m:
                anchor, i = m.group(1), self._ws(i + m.end())
            c = self.t[i:i + 1]
            if c == "!":
                raise YamlUnsupported("tags are not supported")
            if c == "[":
                node, i = self.seq(i + 1)
            elif c == "{":
                node, i = self.map(i + 1)
            elif c in ("'", '"'):
                end = self.p._quoted_end(self.t, i)
                if end is None:
                    raise YamlError("unterminated quoted scalar in a flow collection")
                node, i = self.p._unquote(self.t[i:end + 1]), end + 1
            elif c == "*":
                m = re.match(r"\*([^\s\[\]{},]+)", self.t[i:])
                if not m or m.group(1) not in self.p.anchors:
                    raise YamlError("unknown alias in a flow collection")
                node, i = copy.deepcopy(self.p.anchors[m.group(1)]), i + m.end()
            else:
                node, i = self.plain(i)
            if anchor is not None:
                self.p.anchors[anchor] = node
            return node, self._ws(i)
        finally:
            self.p.depth -= 1

    def plain(self, i: int) -> tuple[str, int]:
        j = i
        while j < len(self.t):
            c = self.t[j]
            if c in ",[]{}":
                break
            if c == ":" and (j + 1 >= len(self.t) or self.t[j + 1] in " ,[]{}"):
                break
            if c == "#" and j > i and self.t[j - 1] == " ":
                raise YamlError("comment inside a flow collection")
            j += 1
        text = self.t[i:j].strip()
        if text[:1] in ("@", "`", "%", "|", ">", "&", "!"):
            raise YamlError(f"flow scalar cannot start with {text[:1]!r}")
        return " ".join(text.split()) if text else "", j

    def seq(self, i: int) -> tuple[list[Any], int]:
        out: list[Any] = []
        i = self._ws(i)
        if self.t[i:i + 1] == "]":
            return out, i + 1
        while True:
            if self.t[i:i + 1] == "?":
                raise YamlUnsupported("complex keys are not supported")
            node, i = self.node(i)
            if self.t[i:i + 1] == ":":
                raise YamlUnsupported("single-pair mappings inside flow sequences are not supported")
            out.append(node)
            c = self.t[i:i + 1]
            if c == "]":
                return out, i + 1
            if c != ",":
                raise YamlError("expected , or ] in a flow sequence")
            i = self._ws(i + 1)
            if self.t[i:i + 1] == "]":
                return out, i + 1

    def map(self, i: int) -> tuple[dict[str, Any], int]:
        out: dict[str, Any] = {}
        i = self._ws(i)
        if self.t[i:i + 1] == "}":
            return out, i + 1
        while True:
            if self.t[i:i + 1] == "?":
                raise YamlUnsupported("complex keys are not supported")
            key, i = self.node(i)
            if not isinstance(key, str):
                raise YamlUnsupported("non-scalar keys are not supported")
            if key == "<<":
                raise YamlUnsupported("merge keys are not supported")
            if self.t[i:i + 1] == ":":
                val, i = self.node(i + 1)
            else:
                val = ""
            out[key] = val
            c = self.t[i:i + 1]
            if c == "}":
                return out, i + 1
            if c != ",":
                raise YamlError("expected , or } in a flow mapping")
            i = self._ws(i + 1)
            if self.t[i:i + 1] == "}":
                return out, i + 1


def _escaped_break(text: str) -> bool:
    """True if text ends with an odd number of backslashes, i.e. an escaped line break."""
    return (len(text) - len(text.rstrip("\\"))) % 2 == 1


def _unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        n = s[i + 1:i + 2]
        if n in _ESC:
            out.append(_ESC[n])
            i += 2
        elif n in ("x", "u", "U"):
            width = {"x": 2, "u": 4, "U": 8}[n]
            hexd = s[i + 2:i + 2 + width]
            if len(hexd) != width or not all(h in "0123456789abcdefABCDEF" for h in hexd):
                raise YamlError("bad escape in a double-quoted scalar")
            out.append(chr(int(hexd, 16)))
            i += 2 + width
        elif n == "\n":
            i += 2
        else:
            raise YamlError(f"unknown escape \\{n}")
    return "".join(out)


def _fold(parts: list[str]) -> str:
    """Join the lines of a multi-line plain scalar: a single break folds to a space, each blank
    line between them becomes a newline."""
    out = parts[0]
    empties = 0
    for p in parts[1:]:
        if p == "":
            empties += 1
            continue
        out += ("\n" * empties if empties else " ") + p
        empties = 0
    return out


def _fold_block(body: list[str]) -> str:
    """Folded block scalar content (YAML 1.2 section 8.1.3): breaks between normal lines become
    spaces, blank lines become newlines, and breaks next to more-indented lines are kept."""
    k = 0
    while k < len(body) and body[k] == "":
        k += 1
    text = "\n" * k
    lines = body[k:]
    i = 0
    while i < len(lines):
        line = lines[i]
        text += line
        j = i + 1
        empties = 0
        while j < len(lines) and lines[j] == "":
            empties += 1
            j += 1
        if j >= len(lines):
            text += "\n" * empties if empties else ""
            break
        nxt = lines[j]
        spaced = line[:1] in (" ", "\t") or nxt[:1] in (" ", "\t")
        if empties == 0:
            text += "\n" if spaced else " "
        else:
            text += "\n" * (empties + 1) if spaced else "\n" * empties
        i = j
    return text


def loads(text: str) -> Node:
    """Parse one YAML document in the subset. Scalars are strings; an empty value is ""."""
    try:
        return _Parser(text).document()
    except RecursionError:
        raise YamlUnsupported("input too deeply nested") from None
