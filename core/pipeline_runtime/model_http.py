"""Route model HTTPS requests without changing the system proxy."""
from __future__ import annotations

import os
import urllib.request
from urllib.parse import urlsplit


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def route(url: str) -> str:
    direct_hosts = {host.strip().lower() for host in
                    os.environ.get("STUDY_MODEL_DIRECT_HOSTS", "").split(",") if host.strip()}
    endpoint = urlsplit(url)
    if endpoint.scheme == "https" and endpoint.hostname in direct_hosts:
        return "direct"
    return "system"


def open_model(request: urllib.request.Request, *, timeout: float):
    # One route per request. An interrupted POST may already be running remotely.
    if route(request.full_url) == "direct":
        return _DIRECT.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)
