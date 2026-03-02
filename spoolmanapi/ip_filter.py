"""IP-based access control for the Spoolman-compatible API endpoints.

Use ``require_ip_access`` as a FastAPI dependency on every Spoolman route.
When IP filtering is disabled in settings, all requests are allowed.
When enabled, the client IP is checked against the configured allow-list
which supports single IPs, CIDR ranges and wildcard ``*`` entries.
"""

from __future__ import annotations

import ipaddress
import logging

from fastapi import HTTPException, Request, status

from .settings import load_settings

_logger = logging.getLogger(__name__)


def _ip_matches(client_ip: str, patterns: list[str]) -> bool:
    """Return True if *client_ip* matches any entry in *patterns*.

    Supported pattern formats:
    - ``*``              → allow everything
    - ``192.168.1.5``    → exact IP match
    - ``192.168.1.0/24`` → CIDR range
    - ``10.0.0.0/8``     → CIDR range
    """
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        _logger.warning("Ungueltige Client-IP: %s", client_ip)
        return False

    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue

        if pattern == "*":
            return True

        try:
            # Try CIDR network first (also matches single IPs like "1.2.3.4/32").
            network = ipaddress.ip_network(pattern, strict=False)
            if addr in network:
                return True
        except ValueError:
            _logger.warning("Ungueltiges IP-Pattern in Allowlist: %s", pattern)
            continue

    return False


async def require_ip_access(request: Request) -> None:
    """FastAPI dependency that enforces the IP allow-list.

    Raises 403 when the client IP is not permitted.
    """
    settings = load_settings()

    if not settings.ip_filter_enabled:
        return  # IP check disabled – allow all

    client_ip = request.client.host if request.client else None
    if client_ip is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client-IP konnte nicht ermittelt werden",
        )

    if not _ip_matches(client_ip, settings.allowed_ips):
        _logger.warning("Spoolman-API Zugriff verweigert fuer IP %s", client_ip)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"IP {client_ip} ist nicht in der Allowlist",
        )
