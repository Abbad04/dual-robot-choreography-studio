"""Stage integrity-checked FAIRINO SDK sources for the macOS connector build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from ur20_timing.fr5_macos_sdk import SDK_SOURCES  # noqa: E402


LICENSE_URL = (
    "https://raw.githubusercontent.com/FAIR-INNOVATION/fairino-python-sdk/"
    "be272581c2ab65f6577f13ee7d0f676c89ab7a25/LICENSE"
)
LICENSE_SIZE = 11_558
LICENSE_SHA256 = "1eb85fc97224598dad1852b5d6483bbcf0aa8608790dcc657a5a2a761ae9c8c6"


def _download_verified(url: str, *, size: int, sha256: str) -> bytes:
    request = Request(url, headers={"User-Agent": "FR5-Connector-Build/1.0"})
    with urlopen(request, timeout=30) as response:
        payload = response.read(size + 1)
    actual_hash = hashlib.sha256(payload).hexdigest()
    if len(payload) != size or actual_hash != sha256:
        raise RuntimeError(
            f"integrity check failed for {url}: size={len(payload)}, sha256={actual_hash}"
        )
    return payload


def stage(destination: Path) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"schema_version": 1, "versions": {}}
    versions = manifest["versions"]
    assert isinstance(versions, dict)
    for version, source in SDK_SOURCES.items():
        payload = _download_verified(
            source.url,
            size=source.size,
            sha256=source.sha256,
        )
        package = destination / version / source.commit / "fairino"
        package.mkdir(parents=True, exist_ok=True)
        (package / "Robot.py").write_bytes(payload)
        versions[version] = {
            "commit": source.commit,
            "size": source.size,
            "sha256": source.sha256,
        }

    license_payload = _download_verified(
        LICENSE_URL,
        size=LICENSE_SIZE,
        sha256=LICENSE_SHA256,
    )
    (destination / "FAIRINO-SDK-LICENSE.txt").write_bytes(license_payload)
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = stage(args.destination.resolve())
    print(f"Staged {len(manifest['versions'])} verified FAIRINO SDK versions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
