"""Input guards run before any ast.parse (architecture section 7.3).

The pre-scan uses tokenize only, bounding the bracket and indentation depth
that ast.parse will face. It does not prove ast.parse safe; the live path
also relies on worker termination for isolation.
"""

from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_bytes: int
    max_lines: int
    max_bracket_depth: int = 100
    max_indent_depth: int = 50

    @classmethod
    def live(cls) -> "Limits":
        """Browser / untrusted-input limits (architecture section 7.3)."""
        return cls(max_bytes=64 * 1024, max_lines=2_000)

    @classmethod
    def cli(cls) -> "Limits":
        """Local CLI and benchmark limits: larger files, same nesting bounds."""
        return cls(max_bytes=2 * 1024 * 1024, max_lines=50_000)

    def to_dict(self) -> dict:
        return {
            "max_bytes": self.max_bytes,
            "max_lines": self.max_lines,
            "max_bracket_depth": self.max_bracket_depth,
            "max_indent_depth": self.max_indent_depth,
        }


class LimitExceeded(Exception):
    """Input is over a limit. Callers turn this into an input_limit gap."""


class Untokenizable(Exception):
    """Input cannot be tokenized. Callers turn this into an unparseable gap."""


_OPEN = {"(", "[", "{"}
_CLOSE = {")", "]", "}"}


def check(source: str, limits: Limits) -> None:
    """Raise LimitExceeded or Untokenizable; return None when the input is within limits."""
    size = len(source.encode("utf-8", errors="surrogatepass"))
    if size > limits.max_bytes:
        raise LimitExceeded(f"{size} bytes exceeds {limits.max_bytes}")
    lines = source.count("\n") + 1
    if lines > limits.max_lines:
        raise LimitExceeded(f"{lines} lines exceeds {limits.max_lines}")
    if "\x00" in source:
        raise Untokenizable("source contains a null byte")
    bracket = indent = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.OP:
                if tok.string in _OPEN:
                    bracket += 1
                    if bracket > limits.max_bracket_depth:
                        raise LimitExceeded(
                            f"bracket nesting exceeds {limits.max_bracket_depth} (line {tok.start[0]})"
                        )
                elif tok.string in _CLOSE:
                    bracket = max(0, bracket - 1)
            elif tok.type == tokenize.INDENT:
                indent += 1
                if indent > limits.max_indent_depth:
                    raise LimitExceeded(
                        f"indentation depth exceeds {limits.max_indent_depth} (line {tok.start[0]})"
                    )
            elif tok.type == tokenize.DEDENT:
                indent = max(0, indent - 1)
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise Untokenizable(f"tokenize failed: {exc}") from None
