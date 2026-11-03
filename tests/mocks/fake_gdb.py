#!/usr/bin/env python3
import sys
import time

for line in sys.stdin:
    command = line.rstrip("\n")
    if command.endswith("-gdb-exit") or command == "-gdb-exit":
        break
    token = ""
    while command and command[0].isdigit():
        token += command[0]
        command = command[1:]
    if command.startswith("-target-select"):
        print('*stopped,reason="breakpoint-hit"', flush=True)
        print(f"{token}^connected", flush=True)
    elif command == "-gdb-show architecture":
        print(f'{token}^done,value="arm"', flush=True)
    elif command == "-data-list-register-names":
        print(f'{token}^done,register-names=["r0","pc"]', flush=True)
    elif command == "-break-list":
        print(f'{token}^done,BreakpointTable={{nr_rows="0"}}', flush=True)
    elif command == "-data-list-register-values x":
        print(f'{token}^done,register-values=[{{number="0",value="0x00"}}]', flush=True)
    elif command.startswith("-exec-continue"):
        print(f"{token}^running", flush=True)
    elif command == "-hang":
        time.sleep(60)
    else:
        print(f'{token}^error,msg="unsupported"', flush=True)
