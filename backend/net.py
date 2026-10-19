"""The visitor's IP address, as far as we can trust it.

In production every request arrives through a Cloudflare Tunnel, so
`request.client.host` is the cloudflared host, not the visitor. Cloudflare
puts the visitor's address in `CF-Connecting-IP`. The app binds 0.0.0.0, so
anyone on the LAN could send that header too: it is only believed when the
peer is listed in TRUSTED_PROXY_IPS (IPs and CIDR ranges, comma-separated;
empty = trust nobody).
"""
from __future__ import annotations

import ipaddress
import logging

from fastapi import Request

from .config import settings

log = logging.getLogger(__name__)

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network

_parsed: tuple[str, list[_Network]] = ("", [])
_warned_untrusted = False


def _trusted_networks() -> list[_Network]:
    """TRUSTED_PROXY_IPS parsed, cached until the setting changes."""
    global _parsed
    raw = settings.trusted_proxy_ips
    if _parsed[0] == raw:
        return _parsed[1]
    networks: list[_Network] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.warning("TRUSTED_PROXY_IPS: ignoring invalid entry %r", part)
    _parsed = (raw, networks)
    return networks


def _is_trusted(peer: str) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_networks())


def client_ip(request: Request, default: str = "") -> str:
    """The visitor's IP: CF-Connecting-IP from a trusted proxy, otherwise
    the TCP peer, otherwise `default`."""
    global _warned_untrusted
    peer = request.client.host if request.client else ""
    header = request.headers.get("cf-connecting-ip", "").strip()
    if header and peer:
        if _is_trusted(peer):
            try:
                return str(ipaddress.ip_address(header))
            except ValueError:
                pass
        elif not _warned_untrusted:
            _warned_untrusted = True
            log.warning(
                "CF-Connecting-IP sent by untrusted peer %s; ignoring it. If this is "
                "the cloudflared host, add it to TRUSTED_PROXY_IPS.", peer,
            )
    return peer or default
