#!/usr/bin/env python3
"""Cross-platform per-user secret storage for web login credentials.

macOS: Login Keychain.
Windows: Current-user DPAPI encrypted blobs under APPDATA.
Linux: FreeDesktop Secret Service (for example GNOME Keyring or KWallet).
The CLI never prints usernames or passwords.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import sys
from contextlib import closing
from ctypes import wintypes
from pathlib import Path

from state_io import atomic_write_bytes

SERVICE = "com.doubao.web-data-to-lark-base"
LINUX_ATTRIBUTES = {"application": SERVICE, "service": SERVICE}
REF_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def validate_ref(secret_ref: str) -> str:
    if not REF_PATTERN.fullmatch(secret_ref):
        raise ValueError("secret_ref_must_use_letters_digits_dot_underscore_hyphen")
    return secret_ref


def backend_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "macos-keychain"
    if system == "Windows":
        return "windows-dpapi-current-user"
    if system == "Linux":
        _linux_check_service()
        return "linux-secret-service"
    raise RuntimeError("unsupported_os_use_enterprise_secret_manager")


def _linux_secretstorage():
    try:
        import secretstorage
    except ImportError as exc:
        raise RuntimeError("linux_secret_service_python_package_missing") from exc
    return secretstorage

def _linux_check_service() -> None:
    secretstorage = _linux_secretstorage()
    try:
        with closing(secretstorage.dbus_init()) as connection:
            if not secretstorage.check_service_availability(connection):
                raise RuntimeError("linux_secret_service_unavailable")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("linux_secret_service_unavailable") from exc

def _linux_attributes(secret_ref: str) -> dict[str, str]:
    return {**LINUX_ATTRIBUTES, "account": secret_ref}

def _linux_unlock(item_or_collection) -> None:
    if item_or_collection.is_locked() and item_or_collection.unlock(timeout=120):
        raise RuntimeError("linux_secret_service_unlock_dismissed")

def _linux_set(secret_ref: str, raw: bytes) -> None:
    secretstorage = _linux_secretstorage()
    try:
        with closing(secretstorage.dbus_init()) as connection:
            if not secretstorage.check_service_availability(connection):
                raise RuntimeError("linux_secret_service_unavailable")
            collection = secretstorage.get_default_collection(connection)
            _linux_unlock(collection)
            collection.create_item(
                f"Web Data to Lark Base: {secret_ref}",
                _linux_attributes(secret_ref),
                raw,
                replace=True,
                content_type="application/json; charset=utf-8",
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("linux_secret_service_write_failed") from exc

def _linux_get(secret_ref: str) -> bytes:
    secretstorage = _linux_secretstorage()
    try:
        with closing(secretstorage.dbus_init()) as connection:
            if not secretstorage.check_service_availability(connection):
                raise RuntimeError("linux_secret_service_unavailable")
            items = list(secretstorage.search_items(connection, _linux_attributes(secret_ref)))
            if not items:
                raise KeyError("credential_not_found")
            item = items[0]
            _linux_unlock(item)
            return item.get_secret()
    except (KeyError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError("linux_secret_service_read_failed") from exc

def _linux_delete(secret_ref: str) -> None:
    secretstorage = _linux_secretstorage()
    try:
        with closing(secretstorage.dbus_init()) as connection:
            if not secretstorage.check_service_availability(connection):
                raise RuntimeError("linux_secret_service_unavailable")
            items = list(secretstorage.search_items(connection, _linux_attributes(secret_ref)))
            if not items:
                raise KeyError("credential_not_found")
            for item in items:
                _linux_unlock(item)
                item.delete()
    except (KeyError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError("linux_secret_service_delete_failed") from exc

def _payload(username: str, password: str) -> bytes:
    if not username or not password:
        raise ValueError("username_and_password_required")
    return json.dumps({"username": username, "password": password}, ensure_ascii=False).encode("utf-8")


def _mac_libs():
    security = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Security.framework/Security")
    core = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    security.SecKeychainAddGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    security.SecKeychainFindGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    security.SecKeychainItemModifyAttributesAndData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    security.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
    security.SecKeychainItemDelete.restype = ctypes.c_int32
    security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    security.SecKeychainItemFreeContent.restype = ctypes.c_int32
    core.CFRelease.argtypes = [ctypes.c_void_p]
    return security, core


def _mac_find(secret_ref: str):
    security, core = _mac_libs()
    service = SERVICE.encode("utf-8")
    account = secret_ref.encode("utf-8")
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(None, len(service), service, len(account), account, ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
    return security, core, status, length, data, item


def _mac_set(secret_ref: str, raw: bytes) -> None:
    security, core, status, _length, data, item = _mac_find(secret_ref)
    if data:
        security.SecKeychainItemFreeContent(None, data)
    if status == 0 and item:
        try:
            result = security.SecKeychainItemModifyAttributesAndData(item, None, len(raw), raw)
            if result != 0:
                raise RuntimeError("keychain_update_failed")
        finally:
            core.CFRelease(item)
        return
    if item:
        core.CFRelease(item)
    service = SERVICE.encode("utf-8")
    account = secret_ref.encode("utf-8")
    result = security.SecKeychainAddGenericPassword(None, len(service), service, len(account), account, len(raw), raw, None)
    if result != 0:
        raise RuntimeError("keychain_write_failed")


def _mac_get(secret_ref: str) -> bytes:
    security, core, status, length, data, item = _mac_find(secret_ref)
    if status != 0:
        if item:
            core.CFRelease(item)
        raise KeyError("credential_not_found")
    try:
        return ctypes.string_at(data, length.value)
    finally:
        security.SecKeychainItemFreeContent(None, data)
        if item:
            core.CFRelease(item)


def _mac_delete(secret_ref: str) -> None:
    security, core, status, _length, data, item = _mac_find(secret_ref)
    if data:
        security.SecKeychainItemFreeContent(None, data)
    if status != 0 or not item:
        if item:
            core.CFRelease(item)
        raise KeyError("credential_not_found")
    try:
        result = security.SecKeychainItemDelete(item)
        if result != 0:
            raise RuntimeError("keychain_delete_failed")
    finally:
        core.CFRelease(item)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(raw: bytes):
    buf = ctypes.create_string_buffer(raw)
    return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def _win_root() -> Path:
    base = os.environ.get("APPDATA")
    if not base:
        raise RuntimeError("APPDATA_not_available")
    path = Path(base) / "WebDataToLarkBase" / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _win_path(secret_ref: str) -> Path:
    digest = hashlib.sha256((SERVICE + "\0" + secret_ref).encode("utf-8")).hexdigest()
    return _win_root() / f"{digest}.bin"


def _win_protect(raw: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, in_buf = _blob(raw)
    entropy, entropy_buf = _blob(SERVICE.encode("utf-8"))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _win_unprotect(raw: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, in_buf = _blob(raw)
    entropy, entropy_buf = _blob(SERVICE.encode("utf-8"))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def set_credential(secret_ref: str, username: str, password: str) -> None:
    secret_ref = validate_ref(secret_ref)
    raw = _payload(username, password)
    backend = backend_name()
    if backend == "macos-keychain":
        _mac_set(secret_ref, raw)
    elif backend == "windows-dpapi-current-user":
        path = _win_path(secret_ref)
        atomic_write_bytes(path, _win_protect(raw))
    elif backend == "linux-secret-service":
        _linux_set(secret_ref, raw)
    else:
        raise RuntimeError("unsupported_secret_backend")


def get_credential(secret_ref: str) -> dict[str, str]:
    secret_ref = validate_ref(secret_ref)
    backend = backend_name()
    if backend == "macos-keychain":
        raw = _mac_get(secret_ref)
    elif backend == "windows-dpapi-current-user":
        raw = _win_unprotect(_win_path(secret_ref).read_bytes())
    elif backend == "linux-secret-service":
        raw = _linux_get(secret_ref)
    else:
        raise RuntimeError("unsupported_secret_backend")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not data.get("username") or not data.get("password"):
        raise ValueError("invalid_credential_payload")
    return {"username": str(data["username"]), "password": str(data["password"])}


def credential_exists(secret_ref: str) -> bool:
    try:
        get_credential(secret_ref)
        return True
    except (KeyError, FileNotFoundError):
        return False
    # 密钥库不可用、未解锁、载荷损坏等不能误报为“凭据不存在”；交给调用方区分处理。


def delete_credential(secret_ref: str) -> None:
    secret_ref = validate_ref(secret_ref)
    backend = backend_name()
    if backend == "macos-keychain":
        _mac_delete(secret_ref)
    elif backend == "windows-dpapi-current-user":
        path = _win_path(secret_ref)
        if not path.exists():
            raise KeyError("credential_not_found")
        path.unlink()
    elif backend == "linux-secret-service":
        _linux_delete(secret_ref)
    else:
        raise RuntimeError("unsupported_secret_backend")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backend", "set", "exists", "delete"])
    parser.add_argument("--ref")
    args = parser.parse_args()

    if args.action == "backend":
        print(json.dumps({"backend": backend_name()}, ensure_ascii=False))
        return 0
    if not args.ref:
        raise SystemExit("secret_ref_required")
    if args.action == "set":
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            raise SystemExit("credential_payload_must_be_object")
        set_credential(args.ref, str(data.get("username", "")), str(data.get("password", "")))
        print(json.dumps({"stored": True, "secret_ref": args.ref, "backend": backend_name()}, ensure_ascii=False))
        return 0
    if args.action == "exists":
        exists = credential_exists(args.ref)
        print(json.dumps({"exists": exists, "secret_ref": args.ref, "backend": backend_name()}, ensure_ascii=False))
        return 0 if exists else 2
    delete_credential(args.ref)
    print(json.dumps({"deleted": True, "secret_ref": args.ref, "backend": backend_name()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
