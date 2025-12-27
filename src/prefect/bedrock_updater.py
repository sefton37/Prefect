from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_BEDROCK_DOWNLOAD_PAGE_URL = "https://www.minecraft.net/en-us/download/server/bedrock"
DEFAULT_BEDROCK_DOWNLOAD_LINKS_API_URL = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"


@dataclass(frozen=True)
class BedrockUpdateResult:
    download_url: str
    backup_dir: Path


def _default_logger(_: str) -> None:
    return


def find_latest_bedrock_linux_download_url(
    *,
    page_url: str = DEFAULT_BEDROCK_DOWNLOAD_PAGE_URL,
    timeout_seconds: float = 20.0,
) -> str:
    """Find the latest Bedrock Dedicated Server (Linux) zip URL.

    Preferred method: query the public download-links API used by minecraft.net.
    Fallback: attempt to scrape the download page (best-effort).
    """

    # 1) Preferred: stable API that returns downloadType -> downloadUrl mappings.
    try:
        req = urllib.request.Request(
            DEFAULT_BEDROCK_DOWNLOAD_LINKS_API_URL,
            headers={
                "User-Agent": "Prefect/BedrockUpdater",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read()

        text = raw.decode("utf-8", errors="replace")
        # Response shape observed:
        # {"result": {"links": [{"downloadType": "serverBedrockLinux", "downloadUrl": "...zip"}, ...]}}
        import json

        payload = json.loads(text)
        links = (payload or {}).get("result", {}).get("links", [])
        for link in links:
            if (link.get("downloadType") or "").lower() == "serverbedrocklinux":
                u = link.get("downloadUrl")
                if isinstance(u, str) and u.startswith("http") and u.endswith(".zip"):
                    return u
    except Exception:
        # Fall back to page scraping below.
        pass

    # 2) Fallback: scrape the download page for a direct link.
    req = urllib.request.Request(
        page_url,
        headers={
            "User-Agent": "Prefect/BedrockUpdater",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        raw = resp.read()

    html = raw.decode("utf-8", errors="replace")

    patterns = (
        # Modern stable hosting (minecraft.net domain)
        r"https?://www\.minecraft\.net/bedrockdedicatedserver/bin-linux/bedrock-server-[^\"'<>\s]+\.zip",
        # Legacy CDN hosting
        r"https?://minecraft\.azureedge\.net/bin-linux/bedrock-server-[^\"'<>\s]+\.zip",
        r"https?://[^\"'<>\s]+/bin-linux/bedrock-server-[^\"'<>\s]+\.zip",
    )

    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            return m.group(0)

    raise RuntimeError(
        "Could not determine the latest Bedrock Linux server download URL. "
        "If this persists, set PREFECT_BEDROCK_DOWNLOAD_URL to the direct .zip link from minecraft.net."
    )


def update_bedrock_server_in_place(
    *,
    server_root: Path,
    download_url: str | None = None,
    download_page_url: str = DEFAULT_BEDROCK_DOWNLOAD_PAGE_URL,
    log: Callable[[str], None] = _default_logger,
) -> BedrockUpdateResult:
    """Download and install the latest Bedrock server over an existing server_root.

    Preserves the world and common config files:
    - worlds/
    - server.properties
    - permissions.json
    - whitelist.json
    - allowlist.json

    Creates a backup directory under server_root/prefect_backups/ for any files it overwrites.
    """

    root = Path(server_root)
    if not root.exists() or not root.is_dir():
        raise RuntimeError(f"bedrock_server_root does not exist or is not a directory: {root}")

    exe = root / "bedrock_server"
    if not exe.exists():
        raise RuntimeError(f"bedrock_server binary not found at: {exe}")

    url = download_url or find_latest_bedrock_linux_download_url(page_url=download_page_url)
    log(f"[BDS:UPDATE] download_url={url}")

    preserve_names = {
        "worlds",
        "server.properties",
        "permissions.json",
        "whitelist.json",
        "allowlist.json",
    }

    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = root / "prefect_backups" / f"bedrock_update_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="prefect-bedrock-update-") as td:
        tmp_dir = Path(td)
        zip_path = tmp_dir / "bedrock_server.zip"
        extract_dir = tmp_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        log("[BDS:UPDATE] downloading...")
        req = urllib.request.Request(url, headers={"User-Agent": "Prefect/BedrockUpdater"})
        with urllib.request.urlopen(req, timeout=60.0) as resp, zip_path.open("wb") as f:
            shutil.copyfileobj(resp, f)
        log(f"[BDS:UPDATE] downloaded_bytes={zip_path.stat().st_size}")

        log("[BDS:UPDATE] extracting...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        # Some zips may wrap content in a top-level folder; others don't.
        candidates = [extract_dir]
        children = [p for p in extract_dir.iterdir()]
        if len(children) == 1 and children[0].is_dir():
            candidates.insert(0, children[0])

        content_root: Path | None = None
        for c in candidates:
            if (c / "bedrock_server").exists():
                content_root = c
                break

        if content_root is None:
            raise RuntimeError("Downloaded zip did not contain a bedrock_server binary")

        log(f"[BDS:UPDATE] installing_from={content_root}")

        # Move aside any destination entries we are about to overwrite.
        for src in content_root.iterdir():
            name = src.name
            if name in preserve_names:
                continue

            dest = root / name
            if dest.exists():
                backup_target = backup_dir / name
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(backup_target))

        # Copy in new version.
        for src in content_root.iterdir():
            name = src.name
            if name in preserve_names:
                # Keep existing worlds/config if present.
                if (root / name).exists():
                    continue

            dest = root / name
            if src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

        # Ensure executable bit for the server binary.
        new_exe = root / "bedrock_server"
        try:
            st = new_exe.stat()
            os.chmod(new_exe, st.st_mode | 0o111)
        except Exception:
            # If chmod fails (e.g., permissions), let the server startup error later.
            pass

        log(f"[BDS:UPDATE] installed ok; backup_dir={backup_dir}")

    return BedrockUpdateResult(download_url=url, backup_dir=backup_dir)
