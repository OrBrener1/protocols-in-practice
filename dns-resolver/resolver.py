#!/usr/bin/env python3
"""Caching resolver for a small DNS-like protocol over UDP.

The resolver has no zone data. It forwards each question to its parent
server and follows "NS" referrals until a server answers with an "A" record
or "non-existent domain". Answers and referrals are cached for TTL seconds,
so a repeated question is answered from memory, and a question about another
name under an already-seen zone goes straight to that zone's server.

Usage:
    python resolver.py PORT PARENT_IP PARENT_PORT [--ttl SECONDS] [--verbose]
"""

import argparse
import socket
import sys
import time
from typing import Dict, Optional, Tuple

BUFFER_SIZE = 4096
DEFAULT_TTL = 60
DEFAULT_DNS_PORT = 53   # used when an NS record carries no port
UPSTREAM_TIMEOUT = 5.0  # seconds to wait for one upstream server
MAX_HOPS = 10           # referrals followed per question before giving up
NOT_FOUND = "non-existent domain"


def parse_server(value: str) -> Tuple[str, int]:
    """Split "ip" or "ip:port" into (ip, port). Raises ValueError on a bad port."""
    if ":" in value:
        ip, port = value.split(":", 1)
        return ip, int(port)
    return value, DEFAULT_DNS_PORT


class Resolver:
    """Resolves domains by following NS referrals, with TTL-based caching."""

    def __init__(self, parent_ip: str, parent_port: int, ttl: int, verbose: bool = False):
        self.parent = (parent_ip, parent_port)
        self.ttl = ttl
        self.verbose = verbose
        # domain -> (reply, expiry time)
        self.answer_cache: Dict[str, Tuple[str, float]] = {}
        # zone suffix such as ".co.il" -> (ip, port, expiry time)
        self.ns_cache: Dict[str, Tuple[str, int, float]] = {}

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)

    def _expire(self, now: float) -> None:
        # Rebuild each cache with only the entries that have not expired yet. The expiry
        # time is the last item of every value: v[1] in answer_cache, v[2] in ns_cache.
        self.answer_cache = {d: v for d, v in self.answer_cache.items() if v[1] > now}
        self.ns_cache = {z: v for z, v in self.ns_cache.items() if v[2] > now}

    def _closest_ns(self, domain: str) -> Optional[Tuple[str, int]]:
        """Return the cached server for the most specific zone containing domain."""
        best, best_len = None, -1
        for suffix, (ip, port, _) in self.ns_cache.items():
            if domain.endswith(suffix) and len(suffix) > best_len:
                best, best_len = (ip, port), len(suffix)
        return best

    @staticmethod
    def _ask(domain: str, ip: str, port: int) -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(UPSTREAM_TIMEOUT)
            sock.sendto(domain.encode(), (ip, port))
            data, _ = sock.recvfrom(BUFFER_SIZE)
        return data.decode(errors="replace").strip()

    def resolve(self, domain: str) -> str:
        """Return an A record for domain, or NOT_FOUND."""
        now = time.time()
        self._expire(now)

        cached = self.answer_cache.get(domain)
        if cached:
            self._log(f"cache hit for {domain}")
            return cached[0]

        server = self._closest_ns(domain) or self.parent
        for _ in range(MAX_HOPS):
            self._log(f"asking {server[0]}:{server[1]} about {domain}")
            try:
                reply = self._ask(domain, *server)
            except OSError:
                # The protocol has no separate error reply, so an unreachable
                # or silent server is reported as NOT_FOUND.
                return NOT_FOUND

            expiry = time.time() + self.ttl
            if reply == NOT_FOUND:
                self.answer_cache[domain] = (reply, expiry)
                return reply

            parts = reply.split(",")
            if len(parts) != 3:
                return NOT_FOUND
            record_domain, value, rtype = parts

            if rtype == "A":
                self.answer_cache[domain] = (reply, expiry)
                return reply
            if rtype == "NS":
                try:
                    ip, port = parse_server(value)
                except ValueError:
                    return NOT_FOUND
                self.ns_cache[record_domain] = (ip, port, expiry)
                server = (ip, port)
                continue
            return NOT_FOUND

        # Referral chain longer than MAX_HOPS, most likely a loop.
        return NOT_FOUND

    def serve(self, port: int) -> None:
        """Answer client questions forever."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("", port))
            while True:
                data, addr = sock.recvfrom(BUFFER_SIZE)
                domain = data.decode(errors="replace").strip()
                sock.sendto(self.resolve(domain).encode(), addr)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Caching resolver for a small DNS-like protocol over UDP.")
    parser.add_argument("port", type=int, help="UDP port to listen on")
    parser.add_argument("parent_ip", help="IP address of the parent server")
    parser.add_argument("parent_port", type=int, help="UDP port of the parent server")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL,
                        help=f"seconds to keep cached records (default {DEFAULT_TTL})")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log cache hits and upstream queries to stderr")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    resolver = Resolver(args.parent_ip, args.parent_port, args.ttl, args.verbose)
    try:
        resolver.serve(args.port)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
