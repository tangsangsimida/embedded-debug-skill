#!/usr/bin/env python3
import os
import socket
import sys
import time

args = sys.argv[1:]
with open(os.environ["EDBG_MOCK_OPENOCD_ARGS"], "w", encoding="utf-8") as handle:
    handle.write("\n".join(args))
port = 3333
host = "127.0.0.1"
for item in args:
    if item.startswith("gdb_port "):
        port = int(item.split()[1])
    if item.startswith("bindto "):
        host = item.split()[1]
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind((host, port))
listener.listen()
try:
    while True:
        time.sleep(0.05)
except KeyboardInterrupt:
    pass
