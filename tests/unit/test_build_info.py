from pathlib import Path

from embedded_debug.build_info import source_digest


def test_source_digest_normalizes_checkout_line_endings(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = package / "module.py"
    source.write_bytes(b"value = 1\nsecond = 2\n")
    lf_digest = source_digest(package)
    source.write_bytes(b"value = 1\r\nsecond = 2\r\n")
    assert source_digest(package) == lf_digest
