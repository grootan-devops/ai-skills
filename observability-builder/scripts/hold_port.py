#!/usr/bin/env python3
"""Hold a TCP port on every local address, to check how an app behaves when its port is taken.

Usage: python3 hold_port.py <port> [--seconds N]

Binds the port on 0.0.0.0, 127.0.0.1, :: and ::1 (IPv6 sockets IPv6-only), so the app can't slip
past on another address family, which happens with dual-stack binds. Prints the addresses held,
then waits until SIGINT, SIGTERM or --seconds. Standard library only.
Exit codes: 0 held at least one address, 1 none could be bound.
"""
from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading

ADDRESSES = ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::"), (socket.AF_INET6, "::1"))


def hold(port: int) -> tuple[list[socket.socket], list[str]]:
    sockets, held = [], []
    for family, host in ADDRESSES:
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            if host in ("127.0.0.1", "::1"):
                # A specific address beside our own wildcard bind needs SO_REUSEADDR on BSD and macOS.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen(1)
            sockets.append(sock)
            held.append(f"[{host}]:{port}" if ":" in host else f"{host}:{port}")
        except OSError:
            sock.close()
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
