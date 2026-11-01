#!/usr/bin/env python3
"""Interactive client for the DNS-like resolver.

Reads a domain name per line and prints its IP address, or
"non-existent domain" when the resolver cannot find it.

Usage:
    python client.py SERVER_IP SERVER_PORT
"""

import argparse
import socket
import sys

BUFFER_SIZE = 4096
TIMEOUT = 5.0  # seconds to wait for a reply
NOT_FOUND = "non-existent domain"


def lookup(sock: socket.socket, server: tuple, domain: str) -> str:
    """Send one question and return the raw reply."""
    sock.sendto(domain.encode(), server)
    data, _ = sock.recvfrom(BUFFER_SIZE)
    return data.decode(errors="replace").strip()


def show(reply: str) -> None:
    """Print the IP for an A record, or the reply itself when it is NOT_FOUND."""
    if reply == NOT_FOUND:
        print(reply)
        return
    parts = reply.split(",")
    if len(parts) == 3 and parts[2] == "A":
        print(parts[1])
    else:
        print(f"unexpected reply: {reply!r}", file=sys.stderr)


def port_number(value: str) -> int:
    """argparse type for a port: an integer from 1 to 65535."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"must be between 1 and 65535, got {port}")
    return port


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive client for the DNS-like resolver.")
    parser.add_argument("server_ip", help="IP address of the resolver")
    parser.add_argument("server_port", type=port_number, help="UDP port of the resolver")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    server = (args.server_ip, args.server_port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(TIMEOUT)
        while True:
            try:
                domain = input("domain> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not domain:
                continue
            try:
                show(lookup(sock, server, domain))
            except socket.timeout:
                print("error: no reply from the resolver (timeout)", file=sys.stderr)


if __name__ == "__main__":
    main()
