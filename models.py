"""Her home model's fingerprint (persona-provider.md 9.5).

A model name is a label: in Ollama a name can be pulled again, or rebuilt, with different weights behind it.
Ollama's model list reports a digest for each model, so where she runs on Ollama the digest is recorded with
what she writes and compared at quiet moments.  Hosted APIs report only a name, and nothing is recorded for them.
Nothing here writes; it asks the local server and reads Hermes' config.
"""
from __future__ import annotations

import ipaddress
import json
import os
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from .chain import model_key

OLLAMA_DEFAULT = "http://localhost:11434"


def configured() -> Tuple[str, str, str]:
    """(provider, model, base_url) of Hermes' configured main model."""
    try:
        from hermes_cli.config import load_config_readonly
        model = (load_config_readonly() or {}).get("model") or {}
    except Exception:
        return "", "", ""
    if isinstance(model, str):
        return "", model, ""
    return (str(model.get("provider") or ""), str(model.get("default") or model.get("model") or ""),
            str(model.get("base_url") or ""))


def _local(url: str) -> bool:
    """Only a server on this machine or the local network is asked: a hosted API has no digest to give."""
    try:
        parts = urllib.parse.urlparse(url)
        host = parts.hostname or ""
    except ValueError:
        return False
    if parts.port == 11434 or host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def server_for(provider: str) -> str:
    """Where to ask for the digest of a model on `provider`, or "" when there is nowhere to ask."""
    cfg_provider, _, base_url = configured()
    url = ""
    if base_url and (not provider or provider.lower() == cfg_provider.lower()):
        url = base_url
    elif provider.lower() in ("ollama", "ollama_chat"):
        url = os.environ.get("OLLAMA_HOST", "") or OLLAMA_DEFAULT
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    url = url.rstrip("/")
    for tail in ("/v1", "/api"):
        if url.endswith(tail):
            url = url[: -len(tail)]
    return url if _local(url) else ""


def fingerprint(provider: str, model: str, timeout: float = 2.0) -> str:
    """The digest Ollama reports for `model`, or "" (not Ollama, not running, or no such model)."""
    if not model:
        return ""
    url = server_for(provider)
    if not url:
        return ""
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=timeout) as r:
            listing = json.loads(r.read().decode("utf-8"))
    except Exception:
        return ""
    want = model_key(model.split("|", 1)[-1])
    for m in listing.get("models") or []:
        if isinstance(m, dict) and model_key(str(m.get("name") or m.get("model") or "")) == want:
            return str(m.get("digest") or "")
    return ""


def short(digest: Optional[str]) -> str:
    d = (digest or "").split(":", 1)[-1]
    return d[:12] if d else "none recorded"
