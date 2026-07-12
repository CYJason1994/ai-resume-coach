"""存储抽象（R2-C1/存储可配置）：local / minio / cos。MVP 默认 local。"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from app.core.config import get_settings
from app.core.errors import StorageError

settings = get_settings()

_EXT = {"pdf": "pdf", "docx": "docx", "doc": "doc", "text": "txt"}


class StorageProvider(ABC):
    @abstractmethod
    async def save(self, storage_key: object, data: bytes, file_type: str) -> str: ...

    @abstractmethod
    async def load(self, storage_key: object, file_type: str) -> bytes: ...

    @abstractmethod
    async def delete(self, storage_key: object, file_type: str) -> None: ...


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_dir: str = settings.STORAGE_LOCAL_DIR) -> None:
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _path(self, storage_key: object, file_type: str) -> str:
        ext = _EXT.get(file_type, "bin")
        return os.path.join(self.base_dir, f"{storage_key}.{ext}")

    async def save(self, storage_key: object, data: bytes, file_type: str) -> str:
        try:
            with open(self._path(storage_key, file_type), "wb") as f:
                f.write(data)
            return str(storage_key)
        except OSError as e:
            raise StorageError(f"本地存储写入失败: {e}") from e

    async def load(self, storage_key: object, file_type: str) -> bytes:
        try:
            with open(self._path(storage_key, file_type), "rb") as f:
                return f.read()
        except OSError as e:
            raise StorageError(f"本地存储读取失败: {e}") from e

    async def delete(self, storage_key: object, file_type: str) -> None:
        try:
            os.remove(self._path(storage_key, file_type))
        except FileNotFoundError:
            pass


class MinioStorageProvider(StorageProvider):
    """占位：生产用 MinIO（docker profile）。M0 不启用。"""

    def __init__(self) -> None:
        raise NotImplementedError("MinIO 存储将在 prod profile 启用（M0 不含）")

    async def save(self, storage_key: object, data: bytes, file_type: str) -> str:
        raise NotImplementedError

    async def load(self, storage_key: object, file_type: str) -> bytes:
        raise NotImplementedError

    async def delete(self, storage_key: object, file_type: str) -> None:
        raise NotImplementedError


_PROVIDER: StorageProvider | None = None


def get_storage() -> StorageProvider:
    global _PROVIDER
    if _PROVIDER is None:
        if settings.STORAGE_PROVIDER == "local":
            _PROVIDER = LocalStorageProvider()
        else:
            raise NotImplementedError(f"存储 provider 未实现: {settings.STORAGE_PROVIDER}")
    return _PROVIDER
