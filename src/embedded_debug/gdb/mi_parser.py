"""Incremental parser for GDB/MI output records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MIRecord:
    token: int | None
    kind: str
    cls: str | None = None
    results: dict[str, Any] | None = None
    text: str | None = None
    raw: str = ""


class MIParseError(ValueError):
    pass


class _Parser:
    def __init__(self, source: str) -> None:
        self.s, self.i = source, 0

    def string(self) -> str:
        self.i += 1
        out: list[str] = []
        while self.i < len(self.s):
            c = self.s[self.i]
            self.i += 1
            if c == '"':
                return "".join(out)
            if c == "\\":
                if self.i >= len(self.s):
                    raise MIParseError("unterminated escape")
                e = self.s[self.i]
                self.i += 1
                mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
                out.append(mapping.get(e, e))
            else:
                out.append(c)
        raise MIParseError("unterminated MI string")

    def value(self) -> Any:
        if self.i >= len(self.s):
            raise MIParseError("missing value")
        if self.s[self.i] == '"':
            return self.string()
        if self.s[self.i] == "{":
            self.i += 1
            return self.tuple("}")
        if self.s[self.i] == "[":
            self.i += 1
            return self.list_value()
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in ",}]":
            self.i += 1
        return self.s[start : self.i]

    def result(self) -> tuple[str, Any]:
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in "=,}]":
            self.i += 1
        if self.i >= len(self.s) or self.s[self.i] != "=":
            raise MIParseError(f"expected result assignment near {self.s[start:]!r}")
        key = self.s[start : self.i]
        self.i += 1
        return key, self.value()

    def tuple(self, end: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while self.i < len(self.s) and self.s[self.i] != end:
            if self.s[self.i] == ",":
                self.i += 1
                continue
            key, value = self.result()
            if key in result:
                prior = result[key]
                result[key] = prior + [value] if isinstance(prior, list) else [prior, value]
            else:
                result[key] = value
        if self.i >= len(self.s):
            raise MIParseError(f"unterminated {end} structure")
        self.i += 1
        return result

    def list_value(self) -> list[Any]:
        values: list[Any] = []
        while self.i < len(self.s) and self.s[self.i] != "]":
            if self.s[self.i] == ",":
                self.i += 1
                continue
            # MI lists may contain values or result assignments.
            if self.s[self.i] in '{["':
                values.append(self.value())
                continue
            save = self.i
            try:
                key, value = self.result()
                values.append({key: value})
            except MIParseError:
                self.i = save
                values.append(self.value())
        if self.i >= len(self.s):
            raise MIParseError("unterminated list")
        self.i += 1
        return values


def parse_record(line: str) -> MIRecord | None:
    raw = line.rstrip("\r\n")
    if not raw or raw == "(gdb)":
        return None
    i = 0
    while i < len(raw) and raw[i].isdigit():
        i += 1
    token = int(raw[:i]) if i else None
    if i >= len(raw):
        return MIRecord(token, "unknown", raw=raw)
    prefix = raw[i]
    i += 1
    if prefix in "^*+=":
        kind_map = {"^": "result", "*": "exec", "+": "status", "=": "notify"}
        end = i
        while end < len(raw) and raw[end] not in ",":
            end += 1
        cls = raw[i:end]
        parser = _Parser(raw[end + 1 :]) if end < len(raw) else _Parser("")
        results: dict[str, Any] = {}
        while parser.i < len(parser.s):
            if parser.s[parser.i] == ",":
                parser.i += 1
                continue
            key, value = parser.result()
            if key in results:
                prior = results[key]
                results[key] = prior + [value] if isinstance(prior, list) else [prior, value]
            else:
                results[key] = value
        return MIRecord(token, kind_map[prefix], cls, results, raw=raw)
    if prefix in "~@&":
        text = _Parser(raw[i:]).string()
        return MIRecord(
            token, {"~": "console", "@": "target", "&": "log"}[prefix], text=text, raw=raw
        )
    return MIRecord(token, "unknown", raw=raw)
