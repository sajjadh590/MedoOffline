"""Tunnel/VPN DNS safety net for the site's host.

Some VPN clients answer every lookup with a fake address from 198.18.0.0/15
and then reset the TLS handshake for hosts they route differently.  Observed
on this machine: ``medofast.ir`` resolved to 198.18.0.94 and every connection
died with ``SSL connect error`` (curl exit 35) until the real address was
pinned.  Chromium inherits the same resolver, so scraping, discovery and the
login window would all fail before the first byte.

When (and only when) the system answer is a fake IP, this module asks
DNS-over-HTTPS for the real A record so the caller can pin it with
``--host-resolver-rules``.  Everything is failure-tolerant: any problem
returns ``None`` and the browser is launched exactly as before.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import time
import urllib.request

DOH_URL = "https://dns.google/resolve?name={host}&type=A"
_FAKE_NETS = (ipaddress.ip_network("198.18.0.0/15"),)


def is_fake_ip(value: str) -> bool:
    """True for the benchmark range VPN clients use as a fake-IP pool."""
    try:
        ip = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return False
    return any(ip in net for net in _FAKE_NETS)


def system_ips(host: str, timeout: float = 3.0) -> list[str]:
    """IPv4 answers from the OS resolver (empty list when it fails)."""
    try:
        infos = socket.getaddrinfo(host, 443, socket.AF_INET,
                                   socket.SOCK_STREAM)
    except OSError:
        return []
    return [info[4][0] for info in infos]


def reachable(target: str, host: str = "medofast.ir", timeout: float = 4.0) -> bool:
    """True when a TLS handshake to `target` (a name or a raw address) works.

    A fake-IP resolver does not by itself mean the address must be pinned: a
    VPN that carries the traffic itself answers the *fake* address fine and
    drops the public one, so pinning there is what breaks the connection.
    Probing both paths is the only way to tell the two apart.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((target, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                return True
    except (OSError, ssl.SSLError):
        return False


def _wininet_proxy() -> tuple[str, str]:
    """(enable, server) straight from Windows' own proxy setting."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enable = str(winreg.QueryValueEx(key, "ProxyEnable")[0])
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
        return enable, server
    except Exception:
        return "0", ""


def proxy_problem(timeout: float = 1.5, connect=None) -> str | None:
    """A running-but-dead Windows proxy, spelled out for the user.

    Chromium obeys the system proxy, so when the setting points at a proxy
    nobody is listening on, *every* page fails — the site then shows its own
    «اینترنت قطع است» notice and downloads stop, while the browser looks
    innocent.  This says which port is dead so the fix is one step.
    """
    enable, server = _wininet_proxy()
    if enable.strip().lstrip("0x") not in {"1"}:
        return None
    host, _, port = server.partition(":")
    if not host:
        return None
    try:
        probe = connect or socket.create_connection
        with probe((host, int(port or 80)), timeout=timeout):
            return None
    except (OSError, ValueError):
        return (f"پروکسی ویندوز روی {server} روشن است ولی جواب نمی‌دهد؛ "
                "یا برنامهٔ VPN/پروکسی را روشن کن یا در Settings → Network → "
                "Proxy گزینهٔ Use a proxy server را خاموش کن.")


def doh_ip(host: str, timeout: float = 6.0) -> str | None:
    """First public A record for `host` via DNS-over-HTTPS, else None."""
    request = urllib.request.Request(
        DOH_URL.format(host=host),
        headers={"accept": "application/dns-json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except Exception:
        return None
    for answer in data.get("Answer", []):
        if answer.get("type") != 1:
            continue
        address = str(answer.get("data", ""))
        if address and not is_fake_ip(address):
            return address
    return None


# A pinned address is reused for a few minutes: every browser launch asks for
# it, and the DoH lookup is the slow part (6 s timeout).  Only *found*
# addresses are cached, so a resolver that recovers is noticed immediately.
_CACHE_TTL_SECONDS = 300.0
_pinned: dict[str, tuple[float, str]] = {}


def clear_cache() -> None:
    """Forget pinned addresses (tests, or after the VPN changes network)."""
    _pinned.clear()


def resolve_public_ip(host: str = "medofast.ir") -> str | None:
    """Address to pin for `host`, or None when the system resolver is fine."""
    cached = _pinned.get(host)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    ips = system_ips(host)
    if ips and not all(is_fake_ip(ip) for ip in ips):
        return None
    if reachable(host):
        return None            # the VPN forwards the fake address itself
    address = doh_ip(host)
    if address and reachable(address):
        _pinned[host] = (time.monotonic(), address)
        return address
    return None


def host_resolver_args(host: str = "medofast.ir") -> list[str]:
    """Chromium args that bypass a fake-IP resolver for one host."""
    ip = resolve_public_ip(host)
    return [f"--host-resolver-rules=MAP {host} {ip}"] if ip else []
