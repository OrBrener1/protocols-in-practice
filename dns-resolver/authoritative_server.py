#!/usr/bin/env python3
"""Authoritative name server for a small DNS-like protocol over UDP.

The server loads a zone file at startup and answers one-line queries, each
containing a domain name.

Zone file format, one record per line, no spaces:

    domain,ip[:port],TYPE

TYPE is "A" (the domain maps to an address) or "NS" (a delegation: every
domain ending with the record's domain is handled by the server at ip:port).

Replies:
    * the matching "A" record, e.g. "biu.ac.il,1.2.3.4,A"
    * otherwise the most specific matching "NS" record
    * "non-existent domain" when nothing matches

Usage:
    python authoritative_server.py PORT ZONE_FILE [--verbose]
"""

import argparse
import socket
import sys
from typing import List, NamedTuple, Optional

BUFFER_SIZE = 4096
NOT_FOUND = "non-existent domain"
RECORD_TYPES = ("A", "NS")


class Record(NamedTuple):
    domain: str
    value: str  # "ip" or "ip:port"
    rtype: str  # "A" or "NS"

    def __str__(self) -> str:
        return f"{self.domain},{self.value},{self.rtype}"


def load_zone(path: str) -> List[Record]:
    """Read a zone file. Malformed lines are skipped with a warning."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            # Split on commas and trim the spaces around each field. A valid record has
            # exactly three fields, a non-empty domain and a known type. The length check
            # comes first so that parts[2] is only read when it exists.
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 3 or not parts[0] or parts[2] not in RECORD_TYPES:
                print(f"warning: {path}:{number}: skipping malformed line {line!r}",
                      file=sys.stderr)
                continue
            domain, value, rtype = parts
            # NS records match by suffix. The leading dot keeps "co.il" from
            # also matching a name such as "xco.il".
            if rtype == "NS" and not domain.startswith("."):
                domain = "." + domain
            records.append(Record(domain, value, rtype))
    return records


def find_answer(records: List[Record], query: str) -> str:
    """Return the reply for a query: an A record, the best NS record, or NOT_FOUND."""
    for record in records:
        if record.rtype == "A" and record.domain == query:
            return str(record)

    best: Optional[Record] = None
    for record in records:
        if record.rtype == "NS" and query.endswith(record.domain):
            if best is None or len(record.domain) > len(best.domain):
                best = record
    return str(best) if best else NOT_FOUND


def serve(port: int, records: List[Record], verbose: bool) -> None:
    """Answer queries forever."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", port))
        while True:
            data, addr = sock.recvfrom(BUFFER_SIZE)
            # Messages are plain text, so encode and decode explicitly;
            # str(data) would give the repr b'...' instead of the text.
            query = data.decode(errors="replace").strip()
            answer = find_answer(records, query)
            if verbose:
                print(f"{addr[0]}:{addr[1]} asked {query!r} -> {answer}", file=sys.stderr)
            sock.sendto(answer.encode(), addr)


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
    parser = argparse.ArgumentParser(
        description="Authoritative name server for a small DNS-like protocol over UDP.")
    parser.add_argument("port", type=port_number, help="UDP port to listen on")
    parser.add_argument("zone_file", help="path to the zone file")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every query to stderr")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    try:
        records = load_zone(args.zone_file)
    except OSError as e:
        sys.exit(f"error: cannot read zone file: {e}")
    try:
        serve(args.port, records, args.verbose)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
