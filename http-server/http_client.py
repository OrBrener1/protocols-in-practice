#!/usr/bin/env python3
"""Interactive HTTP/1.1 client over a raw TCP socket.

Type a path to request it. The status line is printed, and a 200 response body is saved
into the output directory. The connection is kept open between requests (keep-alive),
and reopened automatically if the server has closed it in the meantime.

Prefix a path with "close " to ask the server to close the connection after that request,
for example "close /index.html". Press Ctrl+D or Ctrl+C to quit.

Usage:
    python http_client.py SERVER_IP SERVER_PORT [--output-dir DIR]
"""

import argparse
import os
import socket
from typing import List, Optional, Tuple

BUFFER_SIZE = 4096
TIMEOUT = 5.0  # seconds to wait for the server
PROMPT = "path (or 'close <path>' to end the connection after this request): "


def connect(server: Tuple[str, int]) -> socket.socket:
    """Open a TCP connection to the server."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT)
    try:
        sock.connect(server)
    except OSError:
        sock.close()
        raise
    return sock


def read_response(sock: socket.socket) -> Tuple[List[str], bytes]:
    """Read one response and return (header lines, body).

    Raises ConnectionError if the server closes the connection before the whole
    response, as announced by Content-Length, has arrived.
    """
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(BUFFER_SIZE)
        if chunk == b"":
            raise ConnectionError("server closed the connection")
        data += chunk

    head, _, body = data.partition(b"\r\n\r\n")
    header_lines = head.decode(errors="replace").split("\r\n")

    content_length = 0
    for line in header_lines[1:]:
        if line.lower().startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except ValueError:
                content_length = 0

    while len(body) < content_length:
        chunk = sock.recv(BUFFER_SIZE)
        if chunk == b"":
            raise ConnectionError("server closed the connection mid-body")
        body += chunk
    return header_lines, body


def send_request(sock: socket.socket, path: str, close: bool) -> Tuple[List[str], bytes]:
    """Send one GET request and return the parsed response."""
    connection = "close" if close else "keep-alive"
    request = f"GET {path} HTTP/1.1\r\nConnection: {connection}\r\n\r\n"
    sock.sendall(request.encode())
    return read_response(sock)


def save_body(path: str, body: bytes, output_dir: str) -> str:
    """Write a response body into output_dir and return the file name used."""
    name = os.path.basename(path.split("?", 1)[0]) or "index.html"
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, name), "wb") as f:
        f.write(body)
    return name


def server_wants_close(header_lines: List[str]) -> bool:
    """True if the response carries 'Connection: close'."""
    for line in header_lines[1:]:
        if line.lower().startswith("connection:"):
            return line.split(":", 1)[1].strip().lower() == "close"
    return False


def fetch(sock: Optional[socket.socket], server: Tuple[str, int], path: str,
          close: bool) -> Tuple[Optional[socket.socket], List[str], bytes]:
    """Request path, reusing sock when there is one.

    The server drops idle connections after a short timeout, so a reused connection can
    be dead without us knowing. In that case reconnect and send the request once more.
    Returns the socket to keep for the next request (None if it was closed).
    """
    reused = sock is not None
    if sock is None:
        sock = connect(server)
    try:
        header_lines, body = send_request(sock, path, close)
    except (ConnectionError, socket.timeout):
        sock.close()
        if not reused:
            raise
        sock = connect(server)
        header_lines, body = send_request(sock, path, close)

    if close or server_wants_close(header_lines):
        sock.close()
        sock = None
    return sock, header_lines, body


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive HTTP/1.1 client.")
    parser.add_argument("server_ip")
    parser.add_argument("server_port", type=int)
    parser.add_argument("--output-dir", default="downloads",
                        help="folder for saved response bodies (default: downloads)")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    server = (args.server_ip, args.server_port)
    sock: Optional[socket.socket] = None

    try:
        while True:
            raw = input(PROMPT).strip()
            close = raw.startswith("close ")
            path = raw.split(" ", 1)[1].strip() if close else raw
            if path == "":
                continue
            if not path.startswith("/"):
                path = "/" + path

            try:
                sock, header_lines, body = fetch(sock, server, path, close)
            except OSError as exc:  # includes ConnectionError and timeouts
                print(f"error: {exc}")
                sock = None
                continue

            status_line = header_lines[0]
            print(status_line)
            if status_line.startswith("HTTP/1.1 200"):
                save_body(path, body, args.output_dir)
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        if sock is not None:
            sock.close()


if __name__ == "__main__":
    main()
