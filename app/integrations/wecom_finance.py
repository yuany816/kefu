from __future__ import annotations

import base64
import ctypes
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class WeComFinanceSdkError(RuntimeError):
    pass


class WeComFinanceSdk:
    """Python binding for the official WeWorkFinanceSdk C library.

    The official SDK currently ships native libraries for Linux and Windows.
    The production worker should therefore run on Linux, even if the API is
    developed from macOS.
    """

    def __init__(
        self,
        *,
        library_path: Path,
        corp_id: str,
        archive_secret: str,
        private_key_path: Path,
    ) -> None:
        if not library_path.is_file():
            raise WeComFinanceSdkError(f"Finance SDK 不存在: {library_path}")
        if not private_key_path.is_file():
            raise WeComFinanceSdkError(f"RSA 私钥不存在: {private_key_path}")
        if not corp_id or not archive_secret:
            raise WeComFinanceSdkError("缺少 CorpID 或会话内容存档 Secret")

        self._lib = ctypes.CDLL(str(library_path))
        self._configure_signatures()
        self._private_key = self._load_private_key(private_key_path)
        self._sdk = self._lib.NewSdk()
        if not self._sdk:
            raise WeComFinanceSdkError("NewSdk 返回空指针")
        result = self._lib.Init(
            self._sdk,
            corp_id.encode("utf-8"),
            archive_secret.encode("utf-8"),
        )
        if result != 0:
            self.close()
            raise WeComFinanceSdkError(f"Finance SDK 初始化失败: {result}")

    def close(self) -> None:
        sdk = getattr(self, "_sdk", None)
        if sdk:
            self._lib.DestroySdk(sdk)
            self._sdk = None

    def __enter__(self) -> WeComFinanceSdk:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def pull_decrypted(
        self,
        *,
        seq: int,
        limit: int = 1000,
        timeout_seconds: int = 10,
    ) -> tuple[list[dict[str, Any]], int]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")
        encrypted = self._get_chat_data(seq=seq, limit=limit, timeout_seconds=timeout_seconds)
        messages: list[dict[str, Any]] = []
        max_seq = seq
        for item in encrypted:
            item_seq = int(item["seq"])
            plaintext = self._decrypt_record(item)
            plaintext["_archive_seq"] = item_seq
            plaintext["_publickey_ver"] = int(item.get("publickey_ver", 0))
            messages.append(plaintext)
            max_seq = max(max_seq, item_seq)
        return messages, max_seq

    def _get_chat_data(
        self, *, seq: int, limit: int, timeout_seconds: int
    ) -> list[dict[str, Any]]:
        slice_pointer = self._new_slice()
        try:
            result = self._lib.GetChatData(
                self._sdk,
                ctypes.c_ulonglong(seq),
                ctypes.c_uint(limit),
                b"",
                b"",
                timeout_seconds,
                slice_pointer,
            )
            if result != 0:
                raise WeComFinanceSdkError(f"GetChatData 调用失败: {result}")
            payload = json.loads(self._slice_content(slice_pointer))
            if int(payload.get("errcode", 0)) != 0:
                raise WeComFinanceSdkError(
                    f"GetChatData API 错误 {payload.get('errcode')}: {payload.get('errmsg')}"
                )
            return list(payload.get("chatdata") or [])
        finally:
            self._lib.FreeSlice(slice_pointer)

    def _decrypt_record(self, item: dict[str, Any]) -> dict[str, Any]:
        try:
            encrypted_key = base64.b64decode(item["encrypt_random_key"], validate=True)
            random_key = self._private_key.decrypt(encrypted_key, padding.PKCS1v15())
            encrypted_message = str(item["encrypt_chat_msg"]).encode("utf-8")
        except (KeyError, ValueError, TypeError) as exc:
            raise WeComFinanceSdkError("会话密文格式无效") from exc

        slice_pointer = self._new_slice()
        try:
            result = self._lib.DecryptData(random_key, encrypted_message, slice_pointer)
            if result != 0:
                raise WeComFinanceSdkError(
                    f"DecryptData 调用失败: {result}; 请检查 publickey_ver 对应的私钥"
                )
            return json.loads(self._slice_content(slice_pointer))
        finally:
            self._lib.FreeSlice(slice_pointer)

    def _new_slice(self) -> int:
        pointer = self._lib.NewSlice()
        if not pointer:
            raise WeComFinanceSdkError("NewSlice 返回空指针")
        return pointer

    def _slice_content(self, slice_pointer: int) -> str:
        content = self._lib.GetContentFromSlice(slice_pointer)
        if content is None:
            raise WeComFinanceSdkError("SDK 返回了空 Slice")
        return content.decode("utf-8")

    @staticmethod
    def _load_private_key(path: Path) -> rsa.RSAPrivateKey:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise WeComFinanceSdkError("会话存档私钥必须是 RSA PEM 私钥")
        return key

    def _configure_signatures(self) -> None:
        void_pointer = ctypes.c_void_p
        self._lib.NewSdk.argtypes = []
        self._lib.NewSdk.restype = void_pointer
        self._lib.Init.argtypes = [void_pointer, ctypes.c_char_p, ctypes.c_char_p]
        self._lib.Init.restype = ctypes.c_int
        self._lib.DestroySdk.argtypes = [void_pointer]
        self._lib.DestroySdk.restype = None

        self._lib.NewSlice.argtypes = []
        self._lib.NewSlice.restype = void_pointer
        self._lib.FreeSlice.argtypes = [void_pointer]
        self._lib.FreeSlice.restype = None
        self._lib.GetContentFromSlice.argtypes = [void_pointer]
        self._lib.GetContentFromSlice.restype = ctypes.c_char_p

        self._lib.GetChatData.argtypes = [
            void_pointer,
            ctypes.c_ulonglong,
            ctypes.c_uint,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            void_pointer,
        ]
        self._lib.GetChatData.restype = ctypes.c_int
        self._lib.DecryptData.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            void_pointer,
        ]
        self._lib.DecryptData.restype = ctypes.c_int
