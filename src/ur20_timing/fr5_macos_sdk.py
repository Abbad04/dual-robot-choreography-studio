"""Load FAIRINO's pinned pure-Python SDK source on macOS.

FAIRINO publishes Python SDK releases for Linux and Windows, but each release
also contains the platform-neutral ``Robot.py`` source used to build those
packages.  The connector uses that official source directly on macOS.  It
selects the revision from the controller's reported WebApp version, verifies
the exact byte count and SHA-256 digest, and caches it in the user's Library.

This module performs no robot I/O on import.  Version discovery and download
only happen after the operator presses Connect in the browser.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import importlib
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xmlrpc.client


class MacFairinoSdkError(RuntimeError):
    """The matching official FAIRINO SDK source could not be prepared."""


@dataclass(frozen=True)
class SdkSource:
    commit: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return (
            "https://raw.githubusercontent.com/FAIR-INNOVATION/"
            f"fairino-python-sdk/{self.commit}/linux/fairino/Robot.py"
        )


# Immutable official FAIRINO revisions.  V3.9.4 is the first supported release
# because the connector requires the controller's communication-loss stop.
SDK_SOURCES: dict[str, SdkSource] = {
    "3.9.4": SdkSource(
        "d509a2df89050607bc828993b2633923c0c546a3",
        576_149,
        "cc5b73d6910a2799e5691d8a1986bcfb6b4939d1029ec2cbad8b6e747f704196",
    ),
    "3.9.5": SdkSource(
        "be272581c2ab65f6577f13ee7d0f676c89ab7a25",
        641_438,
        "8dd4ec225a6d1cf149a149c6fdf09e112a5d6fd989426702997bd9cc38252da0",
    ),
    "3.9.6": SdkSource(
        "2e2c6c2220bbdcc5c116b2c831ee70a0ca1c0a8a",
        662_440,
        "73a15acf930bee125b5b2abb15fdea884f222d15a187373d664f08f6ed704577",
    ),
    "3.9.7": SdkSource(
        "d3c93a79d92d73fc276d0ab9d62a27b3d44d08cc",
        679_352,
        "a91ac8d12e876e8a79cf14215aa371fc1288f52582f77fb59c03712f05670391",
    ),
    "3.9.8": SdkSource(
        "563ae32bb3348585c28cf870e293acf7ba6bb286",
        685_495,
        "2e6c9434786cad0a2d61e3103168a18458d5087af4e9a6a8964dd1d51e207de1",
    ),
    "3.9.9": SdkSource(
        "6424f79b581cb5113f604799773314dc10dde411",
        697_720,
        "15eb7b2550d018b2c5bf0770e7e30b0c9627c4edc8fc8bd8eaf2f53a9028ddeb",
    ),
}

_VERSION = re.compile(r"(?<!\d)(3\.9\.[0-9]+)(?!\d)")


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__()
        self.timeout_seconds = float(timeout_seconds)

    def make_connection(self, host: str) -> http.client.HTTPConnection:
        connection = super().make_connection(host)
        connection.timeout = self.timeout_seconds
        return connection


def controller_webapp_version(robot_ip: str, *, timeout_seconds: float = 4.0) -> str:
    """Read the controller's WebApp version without issuing a motion command."""

    try:
        with xmlrpc.client.ServerProxy(
            f"http://{robot_ip}:20003",
            transport=_TimeoutTransport(timeout_seconds),
            allow_none=True,
        ) as proxy:
            response = proxy.GetSoftwareVersion()
    except (OSError, xmlrpc.client.Error) as exc:
        raise MacFairinoSdkError(
            f"the Mac could not read the FR5 software version at {robot_ip}; "
            "check the Ethernet connection and robot power"
        ) from exc

    if not isinstance(response, (tuple, list)) or len(response) < 3:
        raise MacFairinoSdkError("the FR5 returned an invalid software-version response")
    try:
        error_code = int(response[0])
    except (TypeError, ValueError) as exc:
        raise MacFairinoSdkError("the FR5 returned an invalid software-version response") from exc
    if error_code != 0:
        raise MacFairinoSdkError(
            f"the FR5 refused its software-version query (code {error_code})"
        )

    # FAIRINO documents item 2 as the WebApp version.  Search the remaining
    # fields as a compatibility fallback for controller releases that wrap it.
    candidates = [response[2], *response[1:]]
    for candidate in candidates:
        match = _VERSION.search(str(candidate))
        if match:
            return match.group(1)
    raise MacFairinoSdkError(
        "the FR5 response did not contain a recognizable WebApp version"
    )


def _cache_root() -> Path:
    override = os.environ.get("FR5_CONNECTOR_SDK_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "FR5 Choreography Connector"
    return Path.home() / ".cache" / "fr5-choreography-connector"


def _verified(payload: bytes, source: SdkSource) -> bool:
    return len(payload) == source.size and hashlib.sha256(payload).hexdigest() == source.sha256


def _download_source(
    source: SdkSource,
    *,
    opener: Callable[..., object] = urlopen,
) -> bytes:
    request = Request(
        source.url,
        headers={"User-Agent": "FR5-Choreography-Connector/1.0"},
    )
    try:
        response = opener(request, timeout=20)
        with response:  # type: ignore[attr-defined]
            payload = response.read(source.size + 1)  # type: ignore[attr-defined]
    except (HTTPError, URLError, OSError) as exc:
        raise MacFairinoSdkError(
            "the matching FAIRINO SDK could not be downloaded; connect the Mac "
            "to the internet once and try Connect again"
        ) from exc
    if not _verified(payload, source):
        raise MacFairinoSdkError(
            "the downloaded FAIRINO SDK failed its integrity check and was not used"
        )
    return payload


def prepare_official_sdk_source(
    webapp_version: str,
    *,
    cache_root: Path | None = None,
    opener: Callable[..., object] = urlopen,
) -> Path:
    """Return a verified directory that can import ``fairino.Robot``."""

    source = SDK_SOURCES.get(webapp_version)
    if source is None:
        supported = f"{min(SDK_SOURCES)} through {max(SDK_SOURCES)}"
        raise MacFairinoSdkError(
            f"FR5 WebApp {webapp_version} is not supported by this Mac connector; "
            f"supported versions are {supported}"
        )

    root = (cache_root or _cache_root()) / webapp_version / source.commit
    package = root / "fairino"
    robot_source = package / "Robot.py"
    try:
        cached = robot_source.read_bytes()
    except FileNotFoundError:
        cached = b""
    except OSError as exc:
        raise MacFairinoSdkError("the Mac SDK cache could not be read") from exc
    if _verified(cached, source):
        return root

    payload = _download_source(source, opener=opener)
    try:
        package.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=package, prefix="Robot.py.", delete=False
        ) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, robot_source)
        (package / "__init__.py").write_text(
            '"""Pinned official FAIRINO Python SDK source."""\nfrom . import Robot\n',
            encoding="utf-8",
        )
    except OSError as exc:
        raise MacFairinoSdkError("the Mac SDK cache could not be written") from exc
    return root


def load_official_robot_module(robot_ip: str):
    """Load the official FAIRINO Robot module matching the connected controller."""

    version = controller_webapp_version(robot_ip)
    root = prepare_official_sdk_source(version)
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    importlib.invalidate_caches()
    try:
        return importlib.import_module("fairino.Robot")
    except (ImportError, OSError) as exc:
        raise MacFairinoSdkError(
            f"the official FAIRINO SDK for WebApp {version} could not start on this Mac"
        ) from exc


__all__ = [
    "MacFairinoSdkError",
    "SDK_SOURCES",
    "controller_webapp_version",
    "load_official_robot_module",
    "prepare_official_sdk_source",
]
