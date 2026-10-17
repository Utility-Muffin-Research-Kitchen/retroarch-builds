#!/usr/bin/env python3
"""Send GET_STATUS to a RetroArch running a core whose .info has no systemid.

Runs inside the MLP1 toolchain container (see smoke-mlp1-get-status.sh).
Without the get-status-system-id patch, RetroArch dies with SIGSEGV on the
first GET_STATUS after the content loads, so a missing reply is checked
against the process state to tell a crash from a slow start.
"""
import socket
import sys
import time

PORT = 55355
RA_PID = sys.argv[1]
# The test core's library_name, then the content basename without its
# extension.
WANT = "GET_STATUS PLAYING LoadStateSyncTest,game,crc32="


def fail(message):
    print(f"FAIL {message}", file=sys.stderr)
    sys.exit(1)


def retroarch_alive():
    try:
        with open(f"/proc/{RA_PID}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.connect(("127.0.0.1", PORT))


def request(line, timeout=2.0):
    sock.send(line.encode())
    sock.settimeout(timeout)
    try:
        return sock.recv(4096).decode().strip()
    except (socket.timeout, ConnectionRefusedError):
        return None


deadline = time.monotonic() + 30
while True:
    status = request("GET_STATUS")
    if status is not None and status != "GET_STATUS CONTENTLESS":
        break
    if not retroarch_alive():
        fail(f"RetroArch died answering GET_STATUS (last reply {status!r})")
    if time.monotonic() > deadline:
        fail(f"RetroArch never loaded the content (last reply {status!r})")
    time.sleep(0.2)

if not status.startswith(WANT):
    fail(f"GET_STATUS replied {status!r}, want {WANT}...")
print(f"PASS get-status: {status}")

# Twice more, then a different command: still the same process.
for _ in range(2):
    again = request("GET_STATUS")
    if again is None or not again.startswith(WANT):
        fail(f"repeated GET_STATUS replied {again!r}")
version = request("VERSION")
if not version or not retroarch_alive():
    fail(f"RetroArch stopped answering after GET_STATUS (VERSION {version!r})")
print(f"PASS still running: VERSION {version}")

sock.send(b"QUIT")
