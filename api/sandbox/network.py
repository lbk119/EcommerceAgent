"""Network policy validation for sandbox tasks."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from agent.sandbox.models import SandboxNetworkPolicy


BLOCKED_HOSTS = {"localhost", "0.0.0.0", "127.0.0.1", "::1", "169.254.169.254"}
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
]


def validate_network_policy(policy: SandboxNetworkPolicy) -> tuple[bool, str]:
    if policy.mode == "none":
        return True, "network disabled"
    if policy.mode != "allowlist":
        return False, "unknown network mode"
    if not policy.allowed_domains:
        return False, "allowlist network requires allowed_domains"
    for domain in policy.allowed_domains:
        ok, reason = validate_allowed_domain(domain)
        if not ok:
            return False, reason
    return True, "allowlist accepted"


def validate_allowed_domain(value: str) -> tuple[bool, str]:
    text = value.strip().lower()
    if not text:
        return False, "empty domain is not allowed"
    parsed = urlparse(text if "://" in text else f"https://{text}")
    if parsed.scheme not in {"http", "https"}:
        return False, "only http/https domains are allowed"
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        return False, "domain host is required"
    if host in BLOCKED_HOSTS:
        return False, f"blocked host: {host}"
    try:
        address = ipaddress.ip_address(host)
        if any(address in network for network in PRIVATE_NETWORKS):
            return False, f"private or metadata address is not allowed: {host}"
    except ValueError:
        pass
    return True, "domain accepted"