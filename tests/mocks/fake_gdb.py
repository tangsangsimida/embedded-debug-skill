#!/usr/bin/env python3
"""Stateful GDB/MI mock used by process-boundary session integration tests."""

import os
import sys
import threading
import time

_write_lock = threading.Lock()
_breakpoints: list[str] = []
_number = 0
_current_thread = "1"


def emit(line: str) -> None:
    with _write_lock:
        print(line, flush=True)


def log(line: str) -> None:
    path = os.environ.get("EDBG_FAKE_GDB_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


log(f"START {os.getpid()}")
for line in sys.stdin:
    command = line.rstrip("\n")
    original = command
    token = ""
    while command and command[0].isdigit():
        token += command[0]
        command = command[1:]
    log(f"{os.getpid()} {command}")
    crash_on = os.environ.get("EDBG_FAKE_GDB_CRASH_ON", "")
    crash_file = os.environ.get("EDBG_FAKE_GDB_CRASH_FILE")
    if crash_file and os.path.isfile(crash_file):
        crash_on = open(crash_file, encoding="utf-8").read().strip()
    if crash_on and crash_on in command:
        os._exit(42)
    if command == "-gdb-exit":
        break
    if command.startswith("-target-select"):
        emit(f"{token}^connected")
        emit('*stopped,reason="breakpoint-hit",thread-id="1"')
    elif command == "-gdb-show architecture":
        emit(f'{token}^done,value="arm"')
    elif command == "-data-list-register-names":
        emit(f'{token}^done,register-names=["r0","pc"]')
    elif command == "-break-list":
        body = ",".join(
            f'bkpt={{number="{i}",addr="0x08000100",enabled="y"}}'
            for i, _ in enumerate(_breakpoints, 1)
        )
        emit(f'{token}^done,BreakpointTable={{nr_rows="{len(_breakpoints)}",body=[{body}]}}')
    elif command.startswith("-break-insert"):
        _number += 1
        _breakpoints.append(str(_number))
        emit(f'{token}^done,bkpt={{number="{_number}",addr="0x08000100",enabled="y"}}')
    elif command.startswith("-break-delete"):
        number = command.rsplit(" ", 1)[-1]
        if number in _breakpoints:
            _breakpoints.remove(number)
        emit(f"{token}^done")
    elif command == "-data-list-register-values x":
        link_file = os.environ.get("EDBG_FAKE_GDB_LINK_FILE")
        if link_file and os.path.isfile(link_file):
            emit(f'{token}^error,msg="Remote connection closed"')
            continue
        emit(
            f'{token}^done,register-values=[{{number="0",value="0x2a"}},{{number="1",value="0x08000100"}}]'
        )
    elif command.startswith("-data-evaluate-expression"):
        if "slow" in command:
            time.sleep(0.3)
        value = "42" if "fast" not in command else "7"
        emit(f'{token}^done,value="{value}"')
    elif command == "-stack-list-frames":
        emit(f'{token}^done,stack=[frame={{level="0",func="main",addr="0x08000100"}}]')
    elif command == "-thread-info":
        emit(
            f'{token}^done,threads=[{{id="1",target-id="Thread 1",state="stopped"}},'
            f'{{id="2",target-id="Thread 2",state="stopped"}}],'
            f'current-thread-id="{_current_thread}"'
        )
    elif command.startswith("-thread-select "):
        _current_thread = command.rsplit(" ", 1)[-1]
        emit(f'{token}^done,new-thread-id="{_current_thread}"')
    elif command.startswith("-data-read-memory-bytes"):
        emit(f'{token}^done,memory=[{{begin="0x20000000",contents="2a"}}]')
    elif command.startswith("-data-write-memory-bytes"):
        emit(f"{token}^done")
    elif command.startswith("-exec-continue"):
        emit(f"{token}^running")
        emit('*running,thread-id="all"')
        timer = threading.Timer(
            0.08, lambda: emit('*stopped,reason="breakpoint-hit",thread-id="1"')
        )
        timer.daemon = True
        timer.start()
    elif command.startswith("-exec-step") or command.startswith("-exec-next"):
        emit(f"{token}^running")
        timer = threading.Timer(
            0.04, lambda: emit('*stopped,reason="end-stepping-range",thread-id="1"')
        )
        timer.daemon = True
        timer.start()
    elif command.startswith("-exec-interrupt"):
        emit('*stopped,reason="signal-received",signal-name="SIGINT"')
        emit(f"{token}^done")
    elif command == "-hang":
        time.sleep(60)
    elif command.startswith('-interpreter-exec console "monitor reset halt"'):
        emit('*stopped,reason="signal-received",signal-name="SIGTRAP"')
        emit(f"{token}^done")
    elif command.startswith('-interpreter-exec console "load"'):
        emit(f"{token}^done")
    else:
        emit(f'{token}^error,msg="unsupported command: {command}"')
