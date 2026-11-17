"""Windows COM lifecycle and Arena registration helpers."""

from __future__ import annotations

import gc
import logging
import sys
import threading
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Callable

from .errors import ArenaExtractorError, error_text

try:
    import winreg
except ImportError:
    winreg = None

try:
    import pythoncom
    import pywintypes
    import win32api
    from win32com.client import DispatchEx
except ImportError as error:
    pythoncom = None
    pywintypes = None
    win32api = None
    DispatchEx = None
    _COM_IMPORT_ERROR: ImportError | None = error
else:
    _COM_IMPORT_ERROR = None


ARENA_PROG_ID = "Arena.Application"
DEFAULT_COM_TIMEOUT_SECONDS = 20.0
_ARENA_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


def is_com_time(value: Any) -> bool:
    return pywintypes is not None and isinstance(value, pywintypes.TimeType)


def arena_registration() -> dict[str, Any]:
    result: dict[str, Any] = {
        "registered": False,
        "prog_id": ARENA_PROG_ID,
        "clsid": None,
        "executable": None,
        "file_version": None,
    }
    if sys.platform != "win32" or winreg is None:
        result["error"] = "Arena registration checks require Windows."
        return result
    if win32api is None:
        detail = f" {error_text(_COM_IMPORT_ERROR)}" if _COM_IMPORT_ERROR else ""
        result["error"] = f"pywin32 is required for Arena registration checks.{detail}"
        return result

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{ARENA_PROG_ID}\CLSID") as key:
            clsid = winreg.QueryValue(key, None)
        result["clsid"] = clsid
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32"
        ) as key:
            executable = winreg.QueryValue(key, None).strip().strip('"')
        result["executable"] = executable
        if Path(executable).is_file():
            info = win32api.GetFileVersionInfo(executable, "\\")
            ms = info["FileVersionMS"]
            ls = info["FileVersionLS"]
            result["file_version"] = ".".join(
                str(value)
                for value in (
                    win32api.HIWORD(ms),
                    win32api.LOWORD(ms),
                    win32api.HIWORD(ls),
                    win32api.LOWORD(ls),
                )
            )
        result["registered"] = True
    except Exception as error:
        if isinstance(error, OSError) or (
            pywintypes is not None and isinstance(error, pywintypes.error)
        ):
            result["error"] = error_text(error)
        else:
            raise
    return result


def require_com_dependencies() -> tuple[Any, Any, Any, Any]:
    if sys.platform != "win32":
        raise ArenaExtractorError("Arena COM extraction is available only on Windows.")
    if (
        pythoncom is None
        or pywintypes is None
        or win32api is None
        or DispatchEx is None
    ):
        detail = f" {error_text(_COM_IMPORT_ERROR)}" if _COM_IMPORT_ERROR else ""
        raise ArenaExtractorError(f"pywin32 is required for Arena COM extraction.{detail}")
    return pythoncom, pywintypes, win32api, DispatchEx


class ArenaSession(AbstractContextManager["ArenaSession"]):
    """Own one isolated Arena COM instance on one STA thread."""

    def __init__(
        self,
        model_path: Path | None = None,
        lock_timeout: float = DEFAULT_COM_TIMEOUT_SECONDS,
    ) -> None:
        if lock_timeout <= 0:
            raise ValueError("lock_timeout must be greater than zero.")
        self.model_path = model_path
        self.lock_timeout = lock_timeout
        self.application: Any = None
        self.models: Any = None
        self.model: Any = None
        self._locked = False
        self._com_initialized = False

    def __enter__(self) -> "ArenaSession":
        if not _ARENA_LOCK.acquire(timeout=self.lock_timeout):
            raise ArenaExtractorError(
                f"Timed out waiting for another Arena operation after "
                f"{self.lock_timeout:g} seconds."
            )
        self._locked = True
        try:
            pythoncom, _, _, dispatch_ex = require_com_dependencies()
            pythoncom.CoInitialize()
            self._com_initialized = True
            self.application = dispatch_ex(ARENA_PROG_ID)
            self.models = self._wait_for_models()
            if self.model_path is not None:
                self.models.Open(str(self.model_path))
                self.model = self._wait_for_active_model()
                try:
                    self.model.QuietMode = True
                except Exception:
                    pass
            return self
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    def _wait_for(
        self,
        getter: Callable[[], Any],
        description: str,
        timeout: float = DEFAULT_COM_TIMEOUT_SECONDS,
    ) -> Any:
        deadline = time.monotonic() + timeout
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            try:
                value = getter()
                if value is not None:
                    return value
            except Exception as error:
                last_error = error
            require_com_dependencies()[0].PumpWaitingMessages()
            time.sleep(0.1)
        detail = f": {error_text(last_error)}" if last_error else ""
        raise ArenaExtractorError(f"Timed out waiting for {description}{detail}")

    def _wait_for_models(self) -> Any:
        return self._wait_for(lambda: self.application.Models, "Arena Models collection")

    def _wait_for_active_model(self) -> Any:
        return self._wait_for(lambda: self.application.ActiveModel, "Arena ActiveModel")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if self.model is not None:
                try:
                    self.model.Close()
                except Exception as error:
                    _LOGGER.warning("Failed to close Arena model: %s", error_text(error))
            if self.application is not None:
                try:
                    self.application.Quit()
                except Exception as error:
                    _LOGGER.warning(
                        "Failed to quit Arena application: %s", error_text(error)
                    )
        finally:
            self.model = None
            self.models = None
            self.application = None
            try:
                gc.collect()
            finally:
                try:
                    if self._com_initialized:
                        require_com_dependencies()[0].CoUninitialize()
                finally:
                    self._com_initialized = False
                    if self._locked:
                        _ARENA_LOCK.release()
                        self._locked = False
