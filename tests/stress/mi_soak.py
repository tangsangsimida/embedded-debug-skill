"""Opt-in multi-session MI flood/reconnect soak; prints machine-readable evidence."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

import psutil

from embedded_debug.gdb.mi_transport import GDBTimeout, MITransport

MOCK = Path(__file__).resolve().parents[1] / "mocks" / "fake_gdb.py"


def run_worker(deadline: float, output: list[dict[str, int]], errors: list[str]) -> None:
    transport = MITransport(str(MOCK), timeout=5)
    cycles = 0
    try:
        transport.start()
        while time.monotonic() < deadline:
            transport.command("-test-flood")
            assert len(transport.async_records) <= 500
            assert transport._records.qsize() <= 2
            try:
                transport.command('-data-evaluate-expression "slow"', timeout=0.03)
            except GDBTimeout:
                pass
            else:
                raise AssertionError("slow command unexpectedly completed")
            assert transport.command('-data-evaluate-expression "fast"').results["value"] == "7"
            cycles += 1
            if cycles % 5 == 0:
                transport.reconnect()
        output.append({"cycles": cycles, "last_events_dropped": transport.async_records_dropped})
    except Exception as exc:
        errors.append(repr(exc))
    finally:
        transport.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 86400:
        parser.error("--seconds must be between 1 and 86400")
    started = time.monotonic()
    deadline = started + args.seconds
    results: list[dict[str, int]] = []
    errors: list[str] = []
    workers = [
        threading.Thread(target=run_worker, args=(deadline, results, errors)) for _ in range(2)
    ]
    process = psutil.Process(os.getpid())
    initial_rss = process.memory_info().rss
    peak_rss = initial_rss
    for worker in workers:
        worker.start()
    while any(worker.is_alive() for worker in workers):
        peak_rss = max(peak_rss, process.memory_info().rss)
        time.sleep(0.2)
    for worker in workers:
        worker.join()
    print(
        json.dumps(
            {
                "seconds": round(time.monotonic() - started, 2),
                "workers": results,
                "errors": errors,
                "initial_rss": initial_rss,
                "peak_rss": peak_rss,
            }
        )
    )
    return 1 if errors or len(results) != len(workers) else 0


if __name__ == "__main__":
    raise SystemExit(main())
