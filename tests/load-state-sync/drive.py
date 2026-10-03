#!/usr/bin/env python3
"""Drive a running RetroArch through LOAD_STATE_SYNC over the UDP command port.

Runs inside the MLP1 toolchain container next to RetroArch and the test core
(see scripts/smoke-mlp1-load-state-sync.sh). Each case sends one command,
requires exactly one reply, and checks what the core logged, so a refusal that
never reached the core is told apart from one the core made.
"""
import os
import socket
import sys
import time

PORT = 55355
STATES = sys.argv[1]
CORE_LOG = sys.argv[2]
SLOT_PATH = os.path.join(STATES, "game.state99")


def fail(message):
    print(f"FAIL {message}", file=sys.stderr)
    sys.exit(1)


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.connect(("127.0.0.1", PORT))


def drain():
    sock.settimeout(0.05)
    try:
        while True:
            sock.recv(4096)
    except (socket.timeout, ConnectionRefusedError):
        pass


def request(line, timeout=5.0, quiet_after=0.3):
    """Send one command; return its single reply, failing on none or two."""
    drain()
    sock.send(line.encode())
    sock.settimeout(timeout)
    try:
        reply = sock.recv(4096).decode().strip()
    except socket.timeout:
        fail(f"no reply to {line!r}")
    sock.settimeout(quiet_after)
    try:
        extra = sock.recv(4096).decode().strip()
        fail(f"second reply to {line!r}: {extra!r} after {reply!r}")
    except socket.timeout:
        pass
    return reply


def core_log():
    with open(CORE_LOG, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def unserialize_lines():
    return [l for l in core_log().splitlines() if l.startswith("testcore: unserialize")]


def expect(case, line, want):
    before = len(unserialize_lines())
    reply = request(line)
    if reply != want:
        fail(f"{case}: {line!r} replied {reply!r}, want {want!r}")
    print(f"PASS {case}: {reply}")
    return before


def expect_core_untouched(case, before):
    after = unserialize_lines()
    if len(after) != before:
        fail(f"{case}: the core saw the file: {after[before:]}")


def write_slot(data):
    if os.path.isdir(SLOT_PATH):
        os.rmdir(SLOT_PATH)
    with open(SLOT_PATH, "wb") as fh:
        fh.write(data)


# Wait for the command port and a running core.
deadline = time.monotonic() + 30
while True:
    drain()
    sock.send(b"GET_STATUS")
    sock.settimeout(0.5)
    try:
        status = sock.recv(4096).decode().strip()
        if status.startswith("GET_STATUS PLAYING"):
            break
    except (socket.timeout, ConnectionRefusedError):
        pass
    if time.monotonic() > deadline:
        fail("RetroArch never reported PLAYING")
    time.sleep(0.2)
print(f"ready: {status}")

info = request("GET_STATE_SAVE_INFO").split()
if info[:2] != ["GET_STATE_SAVE_INFO", "1"] or info[3] != "0":
    fail(f"unexpected state info {info}")
serialized = int(info[2])

# A real state, written the way Jawaka's power-hold save writes it.
start_by = int(time.monotonic() * 1000) + 5000
reply = request(f"SAVE_STATE_SYNC smokesave 99 {1 << 20} {start_by}").split(" ", 4)
if reply[2] != "TMP_READY":
    fail(f"sync save failed: {reply}")
os.rename(reply[4], SLOT_PATH)
good = open(SLOT_PATH, "rb").read()
saved_counter = [l for l in core_log().splitlines()
                 if l.startswith("testcore: serialize counter=")][-1].split("=")[1]
time.sleep(0.5)  # let the counter move on so the load has something to undo

# OK: exact bytes, and the core applied the saved counter.
expect("ok", "LOAD_STATE_SYNC load-ok 99", f"LOAD_STATE_SYNC load-ok OK {len(good)}")
last = unserialize_lines()[-1]
if last != f"testcore: unserialize ok counter={saved_counter}":
    fail(f"ok: core applied {last!r}, saved counter was {saved_counter}")

# Missing file.
os.unlink(SLOT_PATH)
before = expect("missing", "LOAD_STATE_SYNC load-missing 99",
                "LOAD_STATE_SYNC load-missing ERROR OPEN")
expect_core_untouched("missing", before)

# Truncated in the middle of the core block, and just short of the END block.
for case, cut in (("truncated", len(good) // 2), ("truncated-end", len(good) - 8)):
    write_slot(good[:cut])
    before = expect(case, f"LOAD_STATE_SYNC load-{case} 99",
                    f"LOAD_STATE_SYNC load-{case} ERROR UNSERIALIZE")
    expect_core_untouched(case, before)

# Empty file.
write_slot(b"")
before = expect("empty", "LOAD_STATE_SYNC load-empty 99",
                "LOAD_STATE_SYNC load-empty ERROR UNSERIALIZE")
expect_core_untouched("empty", before)

# Compressed header.
write_slot(b"#RZIPv\x01#" + good[8:])
before = expect("compressed", "LOAD_STATE_SYNC load-rzip 99",
                "LOAD_STATE_SYNC load-rzip ERROR UNSERIALIZE")
expect_core_untouched("compressed", before)

# Intact framing the core itself rejects.
bad = bytearray(good)
bad[40] ^= 0xff
write_slot(bytes(bad))
before = expect("core-rejects", "LOAD_STATE_SYNC load-corrupt 99",
                "LOAD_STATE_SYNC load-corrupt ERROR UNSERIALIZE")
if unserialize_lines()[before:] != ["testcore: unserialize rejected pattern"]:
    fail(f"core-rejects: core log {unserialize_lines()[before:]}")

# Far beyond any state this core writes; sparse, so nothing is allocated.
with open(SLOT_PATH, "wb") as fh:
    fh.truncate(serialized + (17 << 20))
before = expect("too-large", "LOAD_STATE_SYNC load-big 99",
                "LOAD_STATE_SYNC load-big ERROR TOO_LARGE")
expect_core_untouched("too-large", before)

# Not a regular file.
os.unlink(SLOT_PATH)
os.mkdir(SLOT_PATH)
before = expect("directory", "LOAD_STATE_SYNC load-dir 99",
                "LOAD_STATE_SYNC load-dir ERROR OPEN")
expect_core_untouched("directory", before)
os.rmdir(SLOT_PATH)

# Arguments.
expect("bad-id", "LOAD_STATE_SYNC bad!id 99", "LOAD_STATE_SYNC - ERROR BAD_ARGS")
# 33 characters: %32s alone would take 32 and parse the last one as the slot.
expect("long-id", "LOAD_STATE_SYNC " + "1" * 33, "LOAD_STATE_SYNC - ERROR BAD_ARGS")
expect("long-id-slot", "LOAD_STATE_SYNC " + "a" * 33 + " 99",
       "LOAD_STATE_SYNC - ERROR BAD_ARGS")
expect("trailing", "LOAD_STATE_SYNC load-x 99 1", "LOAD_STATE_SYNC - ERROR BAD_ARGS")
expect("slot-range", "LOAD_STATE_SYNC load-slot 1000",
       "LOAD_STATE_SYNC load-slot ERROR BAD_ARGS")
expect("slot-negative", "LOAD_STATE_SYNC load-neg -1",
       "LOAD_STATE_SYNC load-neg ERROR BAD_ARGS")

# The prefix-matched neighbour still answers.
reply = request("LOAD_STATE_SLOT 98")
if reply != "LOAD_STATE_SLOT 98":
    fail(f"LOAD_STATE_SLOT replied {reply!r}")
print(f"PASS load-state-slot: {reply}")

# Nothing above left RetroArch busy or broken: the good state loads again.
write_slot(good)
expect("ok-again", "LOAD_STATE_SYNC load-again 99",
       f"LOAD_STATE_SYNC load-again OK {len(good)}")

sock.send(b"QUIT")
print("PASS load-state-sync smoke")
