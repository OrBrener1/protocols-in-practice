#!/usr/bin/env python3
"""Start the HTTP server and walk through a few requests, printing what comes back.

Usage:
    python examples/run_demo.py

Nothing else is needed; the demo starts and stops its own server on port 8088.
"""

import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "http_server.py")
PORT = 8088


def connect() -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=3)
    return sock


def read_response(sock: socket.socket) -> tuple:
    """Return (status line, headers as a dict, body length)."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:])
    length = int(headers.get("Content-Length", 0))
    while len(body) < length:
        body += sock.recv(4096)
    return lines[0], headers, len(body)


def request(sock: socket.socket, text: str) -> None:
    """Send raw request text, then print the response in short form."""
    first_line = text.split("\r\n")[0]
    sock.sendall(text.encode())
    status, headers, size = read_response(sock)
    extra = [f"{k}: {headers[k]}" for k in ("Content-Type", "Location", "Allow") if k in headers]
    print(f"  > {first_line}")
    print(f"  < {status}  ({size} bytes)" + ("  " + ", ".join(extra) if extra else ""))


def closed_by_server(sock: socket.socket) -> bool:
    """True if the server has closed its side of the connection."""
    sock.settimeout(1)
    try:
        return sock.recv(1) == b""
    except socket.timeout:
        return False


def get(path: str, connection: str = "keep-alive") -> str:
    return f"GET {path} HTTP/1.1\r\nConnection: {connection}\r\n\r\n"


def main() -> None:
    server = subprocess.Popen([sys.executable, "-B", SERVER, str(PORT)],
                              stdout=subprocess.DEVNULL)
    time.sleep(0.7)
    try:
        print("1. Two requests on one keep-alive connection")
        with connect() as s:
            request(s, get("/"))
            request(s, get("/logo.png"))

        print("2. 'Connection: Close' (any capitalization) ends the connection")
        with connect() as s:
            request(s, get("/result.html", "Close"))
            print("  server closed the connection:", closed_by_server(s))

        print("3. Query strings are ignored, a redirect route answers 301")
        with connect() as s:
            request(s, get("/index.html?x=1"))
            request(s, get("/redirect"))

        print("4. Missing file, and a path that tries to leave the served folder")
        with connect() as s:
            request(s, get("/missing.html"))
            request(s, get("/../http_server.py"))
            request(s, get("/%2e%2e/http_server.py"))

        print("5. Only GET is supported")
        with connect() as s:
            request(s, "POST /index.html HTTP/1.1\r\nConnection: keep-alive\r\n\r\n")

        print("6. A malformed request line gets 400, and the server keeps running")
        with connect() as s:
            request(s, "garbage\r\n\r\n")
        with connect() as s:
            request(s, get("/result.html", "close"))

        print("7. Three idle connections are open, another client is still served")
        idle = [connect() for _ in range(3)]
        with connect() as s:
            request(s, get("/result.html", "close"))
        for sock in idle:
            sock.close()
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
