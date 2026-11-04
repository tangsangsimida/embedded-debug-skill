from pathlib import Path

from embedded_debug.doctor import inspect_gdb_mi


def test_mi2_support_is_confirmed_by_response(tmp_path: Path) -> None:
    fake = tmp_path / "gdb-mi.py"
    fake.write_text("import sys\nsys.stdin.read()\nprint('1^done')\n")
    assert inspect_gdb_mi(str(fake)) == "supported"
