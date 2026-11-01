#!/usr/bin/env python3
"""Small HTTP/1.1 file server built directly on TCP sockets.

Serves static files from a folder (default: "files" next to this script). Only GET is
supported. Connections are kept open when the client sends "Connection: keep-alive" and
closed otherwise, or after TIMEOUT seconds without data. A small pool of worker threads
handles connections, so one idle connection (browsers open spare ones) does not block
everybody else.

Usage:
    python http_server.py PORT [--root DIR] [--workers N] [--verbose]
"""

import argparse
import mimetypes
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import unquote

BUFFER_SIZE = 4096
TIMEOUT = 1.0  # seconds a connection may stay silent before the server closes it
DEFAULT_WORKERS = 4
DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")
INDEX = "/index.html"
# Demo route that shows a 301 redirect.
REDIRECTS = {"/redirect": "/result.html"}
REASONS = {
    200: "OK",
    301: "Moved Permanently",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
}


class Request(NamedTuple):
    method: str
    target: str
    headers: Dict[str, str]  # header names in lower case


def parse_request(raw: bytes) -> Request:
    """Parse the request line and headers. Raises ValueError if the request line is malformed."""
    lines = raw.decode(errors="replace").split("\r\n")
    parts = lines[0].split()
    if len(parts) != 3:
        raise ValueError(f"bad request line: {lines[0]!r}")
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return Request(parts[0], parts[1], headers)


def next_request(conn: socket.socket, buffer: bytes) -> Tuple[Optional[bytes], bytes]:
    """Return (one raw request without the blank line, leftover bytes).

    The leftover keeps any bytes of a following request that arrived in the same recv,
    so back-to-back (pipelined) requests are not lost. Returns None as the request when
    the client closes the connection.
    """
    while b"\r\n\r\n" not in buffer:
        chunk = conn.recv(BUFFER_SIZE)
        if chunk == b"":
            return None, buffer
        buffer += chunk
    raw, _, rest = buffer.partition(b"\r\n\r\n")
    return raw, rest


def clean_target(target: str) -> str:
    """Drop the query string and fragment and decode %XX escapes."""
    path = unquote(target.split("?", 1)[0].split("#", 1)[0])
    return INDEX if path == "/" else path


def safe_path(root: str, path: str) -> Optional[str]:
    """Map a URL path to a file inside root, or None if there is no such file.

    Both paths are resolved to real paths first, so "..", and symlinks that point out of
    root, cannot be used to reach files outside it.
    """
    real_root = os.path.realpath(root)
    try:
        candidate = os.path.realpath(os.path.join(real_root, path.lstrip("/")))
    except ValueError:  # e.g. a NUL byte in the path
        return None
    if os.path.commonpath([real_root, candidate]) != real_root:
        return None
    return candidate if os.path.isfile(candidate) else None


def content_type(file_path: str) -> str:
    kind = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    return kind + "; charset=utf-8" if kind.startswith("text/") else kind


def build_response(status: int, headers: List[Tuple[str, str]], body: bytes = b"") -> bytes:
    """Assemble a response. Content-Length is always set, so the client knows where it ends."""
    head = f"HTTP/1.1 {status} {REASONS[status]}\r\n"
    head += "".join(f"{name}: {value}\r\n" for name, value in headers)
    head += f"Content-Length: {len(body)}\r\n\r\n"
    return head.encode() + body


def respond(request: Request, root: str) -> Tuple[bytes, int, bool]:
    """Build the response for one request. Returns (bytes, status, keep_connection_open)."""
    keep = request.headers.get("connection", "").lower() == "keep-alive"
    connection = ("Connection", "keep-alive" if keep else "close")

    if request.method != "GET":
        return build_response(405, [connection, ("Allow", "GET")]), 405, keep

    path = clean_target(request.target)
    if path in REDIRECTS:
        return build_response(301, [connection, ("Location", REDIRECTS[path])]), 301, keep

    file_path = safe_path(root, path)
    if file_path is None:
        return build_response(404, [connection]), 404, keep
    try:
        with open(file_path, "rb") as f:
            body = f.read()
    except OSError:
        return build_response(404, [connection]), 404, keep
    headers = [connection, ("Content-Type", content_type(file_path))]
    return build_response(200, headers, body), 200, keep


def log(request_line: str, status: int, raw: bytes, verbose: bool, port: int) -> None:
    """One summary line per request; under --verbose the full request too, tagged with the
    client's port so requests can be matched to connections."""
    text = f"{request_line} -> {status}"
    if verbose:
        text = raw.decode(errors="replace").rstrip() + f"\n[{port}] " + text
    print(text, flush=True)  # a single print call, so lines from different threads stay whole


def log_connection(port: int, message: str, verbose: bool) -> None:
    """Under --verbose, report when a connection opens and why it closes."""
    if verbose:
        print(f"[{port}] {message}", flush=True)


def handle_connection(conn: socket.socket, addr: Tuple[str, int], root: str, verbose: bool) -> None:
    """Serve requests on one connection until it is closed, times out, or breaks."""
    port = addr[1]
    buffer = b""
    reason = "client closed it"
    try:
        with conn:
            while True:
                raw, buffer = next_request(conn, buffer)
                if raw is None:
                    break
                try:
                    request = parse_request(raw)
                except ValueError:
                    log(raw.split(b"\r\n")[0].decode(errors="replace"), 400, raw, verbose, port)
                    conn.sendall(build_response(400, [("Connection", "close")]))
                    reason = "bad request"
                    break
                response, status, keep = respond(request, root)
                log(f"{request.method} {request.target}", status, raw, verbose, port)
                conn.sendall(response)
                if not keep:
                    reason = "server closed it (no keep-alive)"
                    break
    except socket.timeout:
        reason = f"timeout, no data for {TIMEOUT:g} s"
    except OSError as exc:  # connection reset, broken pipe: just drop this connection
        reason = f"error: {exc}"
    log_connection(port, f"closed ({reason})", verbose)


def serve(port: int, root: str, workers: int, verbose: bool) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Allow restarting right after a stop, instead of waiting for the old port to be released.
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("", port))
    server.listen()
    print(f"Serving {root} on port {port} with {workers} workers", flush=True)

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        while True:
            conn, addr = server.accept()
            conn.settimeout(TIMEOUT)
            log_connection(addr[1], "opened", verbose)  # logged on accept, even if it has to queue
            # With every worker busy, the connection waits in the pool's queue.
            pool.submit(handle_connection, conn, addr, root, verbose)
    except KeyboardInterrupt:
        print("\nStopping", flush=True)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        server.close()


def port_number(value: str) -> int:
    """argparse type for a port: an integer from 1 to 65535."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"must be between 1 and 65535, got {port}")
    return port


def positive_int(value: str) -> int:
    """argparse type for a count that must be at least 1."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Small HTTP/1.1 file server.")
    parser.add_argument("port", type=port_number, help="TCP port to listen on")
    parser.add_argument("--root", default=DEFAULT_ROOT,
                        help="folder to serve (default: 'files' next to this script)")
    parser.add_argument("--workers", type=positive_int, default=DEFAULT_WORKERS,
                        help=f"number of worker threads (default: {DEFAULT_WORKERS})")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every request in full")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    if not os.path.isdir(args.root):
        sys.exit(f"error: not a folder: {args.root}")
    serve(args.port, args.root, args.workers, args.verbose)


if __name__ == "__main__":
    main()
