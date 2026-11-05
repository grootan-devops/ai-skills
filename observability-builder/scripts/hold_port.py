#!/usr/bin/env python3
"""Hold a TCP port on every local address, to check how an app behaves when its port is taken.

Usage: python3 hold_port.py <port> [--seconds N]

Binds the port on the wildcard and loopback addresses of IPv4 and IPv6 (IPv6 sockets IPv6-only, so
the app can't slip past on another address family through a dual-stack bind) and on each local
interface address, then prints the addresses held and waits until SIGINT, SIGTERM or --seconds.

Why every interface address: on macOS and the BSDs a socket that sets SO_REUSEADDR may bind a
specific address beside someone else's wildcard bind, so holding 0.0.0.0 alone does not make the
port "taken". There the specific addresses are bound first, without SO_REUSEADDR, and the wildcard
after them; an exact duplicate bind fails on every platform. On Linux a listening wildcard socket
already blocks every later bind of the port, SO_REUSEADDR or not, so the wildcard goes first. On
Windows the sockets set SO_EXCLUSIVEADDRUSE.

For app code: a metrics or profiling server must not set SO_REUSEADDR (Python's
`allow_reuse_address`, Node's `exclusive: false` cluster sharing, SO_REUSEPORT): with it, the server
can start beside another process on macOS, or share the port on Linux, instead of failing loudly
when its port is taken (on Linux SO_REUSEADDR alone only allows a rebind over TIME_WAIT, which a
metrics server can do without). Standard library only.
Exit codes: 0 held at least one address, 1 none could be bound.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading

WILDCARDS = ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::"))
LOOPBACKS = ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"))
BSD = sys.platform == "darwin" or "bsd" in sys.platform


def local_addresses() -> list[tuple[int, str]]:
    """Unicast interface addresses of this host (link-local IPv6 left out: it needs a scope id)."""
    found: set[tuple[int, str]] = set()
    try:
        for family, _, _, _, addr in socket.getaddrinfo(socket.gethostname(), None, proto=socket.IPPROTO_TCP):
            found.add((family, addr[0]))
    except OSError:
        pass
    for family, target in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET6, "2001:db8::1")):
        try:   # the address the default route would use; a UDP connect sends nothing
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect((target, 9))
                found.add((family, probe.getsockname()[0]))
        except OSError:
            pass
    tool = shutil.which("ifconfig") if BSD else None
    if tool:
        try:
            out = subprocess.run([tool], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for kind, addr in re.findall(r"^\s*(inet6?)\s+([0-9A-Fa-f:.]+)", out, re.MULTILINE):
            found.add((socket.AF_INET6 if kind == "inet6" else socket.AF_INET, addr))
    result = []
    for family, addr in sorted(found):
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            continue
        if ip.is_link_local or ip.is_unspecified or ip.is_multicast or str(ip) in ("127.0.0.1", "::1"):
            continue
        result.append((family, str(ip)))
    return result


def _bind(family: int, host: str, port: int, reuse: bool) -> socket.socket | None:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):   # Windows: nobody may bind this port beside us
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        elif reuse:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def hold(port: int) -> tuple[list[socket.socket], list[str]]:
    specific = list(LOOPBACKS) + local_addresses()
    sockets, held = [], []

    def take(family: int, host: str, reuse_fallback: bool) -> None:
        sock = _bind(family, host, port, reuse=False)
        if sock is None and reuse_fallback:
            sock = _bind(family, host, port, reuse=True)
        if sock is not None:
            sockets.append(sock)
            held.append(f"[{host}]:{port}" if ":" in host else f"{host}:{port}")

    if BSD:
        # Specific addresses first, never with SO_REUSEADDR, so a reusing app bind on them is an exact duplicate.
        for family, host in specific:
            take(family, host, reuse_fallback=False)
        for family, host in WILDCARDS:   # beside our own specific binds the wildcard needs SO_REUSEADDR here
            take(family, host, reuse_fallback=True)
    else:
        for family, host in WILDCARDS + tuple(specific):
            take(family, host, reuse_fallback=False)
    return sockets, held


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port", type=int)
    parser.add_argument("--seconds", type=float, default=0, help="release after this many seconds (default: until SIGINT/SIGTERM)")
    args = parser.parse_args(argv)
    sockets, held = hold(args.port)
    if not held:
        print(f"could not bind port {args.port} on any address", file=sys.stderr)
        return 1
    print("holding " + ", ".join(held), flush=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait(args.seconds or None)
    for sock in sockets:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
