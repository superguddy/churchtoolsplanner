"""Gespeicherter Login (Ersatz fuer Security/LoginStore.cs).

Unter Windows wird das Passwort wie im Original per DPAPI (CurrentUser) verschluesselt,
ohne zusaetzliches Paket (ctypes). Auf anderen Systemen wird nur der Benutzername gemerkt.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    src, out = _blob(data), _DataBlob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI-Aufruf fehlgeschlagen")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_available() -> bool:
    return os.name == "nt"


class LoginStore:
    def __init__(self, path: Path, legacy_path: Path | None = None):
        self.path = path
        self.legacy_path = legacy_path  # login.json der alten C#-App (%APPDATA%\ChurchToolsPlanner)

    def load(self) -> tuple[str, str | None]:
        """(Benutzername, Passwort oder None)."""
        source = self.path if self.path.exists() or not self.legacy_path else self.legacy_path
        try:
            raw = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return "", None
        data = {str(k).lower(): v for k, v in raw.items()}  # C# schrieb "Username"/"PasswordProtectedBase64"
        username = data.get("username") or ""
        protected = data.get("passwordprotectedbase64")
        if not protected or not dpapi_available():
            return username, None
        try:
            return username, _dpapi(base64.b64decode(protected), protect=False).decode("utf-8")
        except (OSError, ValueError):
            return username, None

    def save(self, username: str, password: str) -> None:
        data: dict[str, str] = {"username": username}
        if dpapi_available():
            data["passwordProtectedBase64"] = base64.b64encode(
                _dpapi(password.encode("utf-8"), protect=True)).decode("ascii")
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
