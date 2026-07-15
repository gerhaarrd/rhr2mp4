"""Post-render actions: pushing a finished video to a webhook.

Discord-compatible: small files are attached directly (multipart upload,
which Discord caps at 8MB for regular webhooks); bigger ones fall back to a
text-only message with the file's name/size so the notification still fires.
Uses only the standard library (urllib) so frozen builds gain no new
dependency.
"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid

DISCORD_UPLOAD_LIMIT_MB = 8.0


def send_webhook(url: str, message: str, file_path: str | None = None,
                 timeout: float = 60.0) -> None:
    """POSTs `message` (and the file, when it fits the upload limit) to a
    Discord-style webhook. Raises RuntimeError on failure."""
    if not url.lower().startswith(("http://", "https://")):
        raise RuntimeError(f"invalid webhook url: {url!r}")

    attach = None
    if file_path and os.path.isfile(file_path):
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if size_mb <= DISCORD_UPLOAD_LIMIT_MB:
            attach = file_path
        else:
            message = (f"{message}\n(file too large to attach: "
                       f"{os.path.basename(file_path)}, {size_mb:.1f} MB)")

    if attach is None:
        body = json.dumps({"content": message[:2000]}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "rhr2mp4"})
    else:
        boundary = uuid.uuid4().hex
        with open(attach, "rb") as f:
            file_bytes = f.read()
        payload = json.dumps({"content": message[:2000]})
        parts = [
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="payload_json"\r\n'
            f"Content-Type: application/json\r\n\r\n{payload}\r\n".encode(),
            (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="files[0]"; '
             f'filename="{os.path.basename(attach)}"\r\n'
             f"Content-Type: application/octet-stream\r\n\r\n").encode(),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        req = urllib.request.Request(
            url, data=b"".join(parts), method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "User-Agent": "rhr2mp4"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"webhook returned HTTP {resp.status}")
    except Exception as e:
        raise RuntimeError(f"webhook delivery failed: {e}") from e
