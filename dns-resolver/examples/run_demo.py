#!/usr/bin/env python3
"""Runs the three programs on localhost and walks through a few lookups.

It starts two authoritative servers and a resolver, sends questions through
the client code, and prints what the resolver did for each one (cache hit,
which server it asked, referral to another zone).

Run it from the dns-resolver folder:

    python examples/run_demo.py

To save the output:

    python examples/run_demo.py > examples/demo_output.txt

Ports used: 55555 (parent), 7777 (zone server), 12345 (resolver). The zone
server port must match the NS record in zones/example.zone.
"""

import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from client import NOT_FOUND, lookup  # noqa: E402

PARENT_PORT = 55555
ZONE_PORT = 7777
RESOLVER_PORT = 12345
TTL = 3  # seconds, short so the demo can show a cache expiring


def start(args, log):
    """Start a Python program from the repo folder and collect its stderr lines in log."""
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def pump():
        for line in proc.stderr:
            log.put(line.rstrip())

    threading.Thread(target=pump, daemon=True).start()
    return proc


def drain(log):
    lines = []
    while True:
        try:
            lines.append(log.get_nowait())
        except queue.Empty:
            return lines


def ip_of(reply):
    parts = reply.split(",")
    if reply == NOT_FOUND:
        return reply
    if len(parts) == 3 and parts[2] == "A":
        return parts[1]
    return f"unexpected reply {reply!r}"


def main():
    log = queue.Queue()
    procs = [
        start(["authoritative_server.py", str(PARENT_PORT), "zones/example.zone"], log),
        start(["authoritative_server.py", str(ZONE_PORT), "zones/google.zone"], log),
        start(["resolver.py", str(RESOLVER_PORT), "127.0.0.1", str(PARENT_PORT),
               "--ttl", str(TTL), "--verbose"], log),
    ]
    try:
        time.sleep(0.7)
        startup = drain(log)
        if any(p.poll() is not None for p in procs):
            print("\n".join(startup), file=sys.stderr)
            sys.exit("error: a program exited early. Are ports "
                     f"{PARENT_PORT}, {ZONE_PORT} and {RESOLVER_PORT} free?")

        print("DNS resolver demo")
        print(f"  parent server : 127.0.0.1:{PARENT_PORT}  (zones/example.zone)")
        print(f"  zone server   : 127.0.0.1:{ZONE_PORT}   (zones/google.zone)")
        print(f"  resolver      : 127.0.0.1:{RESOLVER_PORT}  (TTL {TTL} s)")

        server = ("127.0.0.1", RESOLVER_PORT)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(5.0)

            def step(title, domain):
                print(f"\n{title}")
                print(f"  query    : {domain}")
                reply = lookup(sock, server, domain)
                time.sleep(0.2)  # let the resolver's log lines arrive
                for line in drain(log):
                    print(f"  resolver : {line}")
                print(f"  answer   : {ip_of(reply)}")

            step("1. First lookup. Nothing is cached, so the resolver asks its parent.",
                 "biu.ac.il")
            step("2. The same name again, within the TTL. Answered from the cache.",
                 "biu.ac.il")
            step("3. A name the parent does not know.",
                 "www.biu.ac.il")
            step("4. A name under .co.il. The parent refers the resolver to the zone\n"
                 "   server, which holds the answer.",
                 "mail.google.co.il")
            step("5. Another name under .co.il. The referral is cached, so the parent\n"
                 "   is skipped.",
                 "www.google.co.il")

            wait = TTL + 0.5
            print(f"\n(waiting {wait:g} s for the cache to expire)")
            time.sleep(wait)
            drain(log)
            step("6. After the TTL the cached records are gone. The parent is asked again.",
                 "www.google.co.il")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=3)


if __name__ == "__main__":
    main()
