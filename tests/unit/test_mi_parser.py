from embedded_debug.gdb.mi_parser import parse_record


def test_result_record_nested_and_escaped_strings() -> None:
    record = parse_record(
        '12^done,stack=[frame={level="0",func="main",args=[{name="x",value="a\\n b"}]}]'
    )
    assert record is not None
    assert record.kind == "result" and record.token == 12
    frame = record.results["stack"][0]["frame"]
    assert frame["func"] == "main"
    assert frame["args"][0]["name"] == "x"
    assert frame["args"][0]["value"] == "a\n b"


def test_async_and_stream_records() -> None:
    assert parse_record('*stopped,reason="breakpoint-hit"').cls == "stopped"
    assert parse_record('=thread-created,id="1"').kind == "notify"
    assert parse_record('~"hello\\n"').text == "hello\n"


def test_prompt_and_empty_lines_are_ignored() -> None:
    assert parse_record("(gdb)") is None
    assert parse_record("\n") is None


def test_mi_string_escapes_in_expressions_are_quoted() -> None:
    from embedded_debug.gdb.controller import mi_quote

    assert mi_quote('x"\\\nnew') == '"x\\"\\\\\\nnew"'
