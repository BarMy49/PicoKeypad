from dataclasses import dataclass
import asyncio
import ctypes
import importlib
import platform
import threading
import time
import unicodedata
import uuid


@dataclass
class VolumeStatus:
    level: int
    muted: bool
    available: bool = True
    error: str = ""


@dataclass
class MediaStatus:
    title: str = ""
    artist: str = ""
    status: str = ""
    available: bool = True
    error: str = ""


_last_media_status: MediaStatus | None = None
_last_media_status_at = 0.0
_MEDIA_CACHE_SECONDS = 8.0


def get_volume_status() -> VolumeStatus:
    if platform.system() != "Windows":
        return VolumeStatus(0, False, False, "volume status is only implemented for Windows")

    try:
        return _get_windows_volume_status()
    except Exception as exc:
        return VolumeStatus(0, False, False, str(exc))


def get_media_status() -> MediaStatus:
    if platform.system() != "Windows":
        return MediaStatus(available=False, error="media status is only implemented for Windows")

    result: dict[str, MediaStatus] = {}
    error: list[str] = []

    def worker() -> None:
        try:
            result["status"] = asyncio.run(_get_windows_media_status_with_retry())
        except Exception as exc:
            error.append(str(exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=2.2)

    if "status" in result:
        return result["status"]

    if error:
        return MediaStatus(available=False, error=error[0])

    return MediaStatus(available=False, error="media status timed out")


def volume_display_lines(status: VolumeStatus) -> list[str]:
    if not status.available:
        return ["VOLUME", "UNAVAILABLE", status.error[:16]]

    filled = int(round(status.level / 10))
    bar = "#" * filled + "-" * (10 - filled)
    text = "MUTED" if status.muted else "{}%".format(status.level)
    return ["VOLUME", "[" + bar + "]", text]


def media_display_lines(status: MediaStatus) -> list[str]:
    if not status.available:
        return ["MEDIA", "UNAVAILABLE", status.error[:16]]

    if not _media_status_has_track(status):
        playback = _playback_label(status.status)
        return [("MEDIA " + playback).strip()[:16], "No media"]

    title, artist = _split_media_title_artist(status.title, status.artist)
    playback = _playback_label(status.status)
    lines = [("MEDIA " + playback).strip()[:16]]

    if title:
        lines.append(_oled_line(title))
    if artist:
        lines.append(_oled_line(artist))

    return lines[:4]


def _split_media_title_artist(title: str, artist: str) -> tuple[str, str]:
    title = _clean_media_text(title)
    artist = _clean_media_text(artist)

    if artist:
        return title or "No title", artist

    for separator in (" - ", " – ", " — ", " | "):
        if separator in title:
            left, right = title.split(separator, 1)
            left = left.strip()
            right = right.strip()
            if left and right:
                return right, left

    return title or "No title", ""


def _clean_media_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    return " ".join(value.split())


def _oled_line(value: str) -> str:
    value = _clean_media_text(value)
    if len(value) <= 16:
        return value
    return value[:15] + "."


def _playback_label(value: str) -> str:
    text = str(value or "").strip()
    key = text.split(".")[-1].strip().lower()

    labels = {
        "0": "CLOSED",
        "1": "OPEN",
        "2": "LOAD",
        "3": "STOP",
        "4": "PLAY",
        "5": "PAUSE",
        "closed": "CLOSED",
        "opened": "OPEN",
        "changing": "LOAD",
        "stopped": "STOP",
        "playing": "PLAY",
        "paused": "PAUSE",
    }
    return labels.get(key, key.upper()[:8] if key else "")


def _media_status_has_track(status: MediaStatus) -> bool:
    if not status.available:
        return False

    title = _clean_media_text(status.title).lower()
    artist = _clean_media_text(status.artist).lower()
    if title in ("", "no session", "no title") and not artist:
        return False

    return bool(title or artist)


def _cache_media_status(status: MediaStatus) -> MediaStatus:
    global _last_media_status, _last_media_status_at

    if _media_status_has_track(status):
        _last_media_status = status
        _last_media_status_at = time.monotonic()

    return status


def _cached_media_status() -> MediaStatus | None:
    if _last_media_status is None:
        return None

    if time.monotonic() - _last_media_status_at > _MEDIA_CACHE_SECONDS:
        return None

    return _last_media_status


class GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    )

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        parsed = uuid.UUID(value)
        data4 = (ctypes.c_ubyte * 8).from_buffer_copy(parsed.bytes[8:])
        return cls(parsed.time_low, parsed.time_mid, parsed.time_hi_version, data4)


def _check_hresult(result: int, where: str) -> None:
    if result < 0:
        raise OSError("{} failed with HRESULT 0x{:08x}".format(where, result & 0xFFFFFFFF))


def _get_windows_volume_status() -> VolumeStatus:
    from ctypes import wintypes

    ole32 = ctypes.OleDLL("ole32")
    HRESULT = getattr(wintypes, "HRESULT", ctypes.c_long)
    ULONG = getattr(wintypes, "ULONG", ctypes.c_ulong)
    WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    CLSCTX_ALL = 0x17
    E_RENDER = 0
    E_MULTIMEDIA = 1

    CLSID_MMDEVICE_ENUMERATOR = GUID.from_string("BCDE0395-E52F-467C-8E3D-C4579291692E")
    IID_IMMDEVICE_ENUMERATOR = GUID.from_string("A95664D2-9614-4F35-A746-DE8DB63617E6")
    IID_IAUDIO_ENDPOINT_VOLUME = GUID.from_string("5CDF2C82-841E-4546-9722-0CF74078229A")

    class IMMDeviceEnumerator(ctypes.Structure):
        pass

    class IMMDevice(ctypes.Structure):
        pass

    class IAudioEndpointVolume(ctypes.Structure):
        pass

    LP_ENUM = ctypes.POINTER(IMMDeviceEnumerator)
    LP_DEVICE = ctypes.POINTER(IMMDevice)
    LP_VOLUME = ctypes.POINTER(IAudioEndpointVolume)

    class IMMDeviceEnumeratorVtbl(ctypes.Structure):
        _fields_ = (
            ("QueryInterface", WINFUNCTYPE(HRESULT, LP_ENUM, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))),
            ("AddRef", WINFUNCTYPE(ULONG, LP_ENUM)),
            ("Release", WINFUNCTYPE(ULONG, LP_ENUM)),
            ("EnumAudioEndpoints", ctypes.c_void_p),
            ("GetDefaultAudioEndpoint", WINFUNCTYPE(HRESULT, LP_ENUM, ctypes.c_int, ctypes.c_int, ctypes.POINTER(LP_DEVICE))),
            ("GetDevice", ctypes.c_void_p),
            ("RegisterEndpointNotificationCallback", ctypes.c_void_p),
            ("UnregisterEndpointNotificationCallback", ctypes.c_void_p),
        )

    class IMMDeviceVtbl(ctypes.Structure):
        _fields_ = (
            ("QueryInterface", WINFUNCTYPE(HRESULT, LP_DEVICE, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))),
            ("AddRef", WINFUNCTYPE(ULONG, LP_DEVICE)),
            ("Release", WINFUNCTYPE(ULONG, LP_DEVICE)),
            ("Activate", WINFUNCTYPE(HRESULT, LP_DEVICE, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))),
            ("OpenPropertyStore", ctypes.c_void_p),
            ("GetId", ctypes.c_void_p),
            ("GetState", ctypes.c_void_p),
        )

    class IAudioEndpointVolumeVtbl(ctypes.Structure):
        _fields_ = (
            ("QueryInterface", WINFUNCTYPE(HRESULT, LP_VOLUME, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))),
            ("AddRef", WINFUNCTYPE(ULONG, LP_VOLUME)),
            ("Release", WINFUNCTYPE(ULONG, LP_VOLUME)),
            ("RegisterControlChangeNotify", ctypes.c_void_p),
            ("UnregisterControlChangeNotify", ctypes.c_void_p),
            ("GetChannelCount", ctypes.c_void_p),
            ("SetMasterVolumeLevel", ctypes.c_void_p),
            ("SetMasterVolumeLevelScalar", ctypes.c_void_p),
            ("GetMasterVolumeLevel", ctypes.c_void_p),
            ("GetMasterVolumeLevelScalar", WINFUNCTYPE(HRESULT, LP_VOLUME, ctypes.POINTER(ctypes.c_float))),
            ("SetChannelVolumeLevel", ctypes.c_void_p),
            ("SetChannelVolumeLevelScalar", ctypes.c_void_p),
            ("GetChannelVolumeLevel", ctypes.c_void_p),
            ("GetChannelVolumeLevelScalar", ctypes.c_void_p),
            ("SetMute", ctypes.c_void_p),
            ("GetMute", WINFUNCTYPE(HRESULT, LP_VOLUME, ctypes.POINTER(wintypes.BOOL))),
            ("GetVolumeStepInfo", ctypes.c_void_p),
            ("VolumeStepUp", ctypes.c_void_p),
            ("VolumeStepDown", ctypes.c_void_p),
            ("QueryHardwareSupport", ctypes.c_void_p),
            ("GetVolumeRange", ctypes.c_void_p),
        )

    IMMDeviceEnumerator._fields_ = (("lpVtbl", ctypes.POINTER(IMMDeviceEnumeratorVtbl)),)
    IMMDevice._fields_ = (("lpVtbl", ctypes.POINTER(IMMDeviceVtbl)),)
    IAudioEndpointVolume._fields_ = (("lpVtbl", ctypes.POINTER(IAudioEndpointVolumeVtbl)),)

    initialized = False
    enumerator = LP_ENUM()
    device = LP_DEVICE()
    volume = LP_VOLUME()

    try:
        hr = ole32.CoInitialize(None)
        initialized = hr >= 0

        _check_hresult(ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDEVICE_ENUMERATOR),
            None,
            CLSCTX_ALL,
            ctypes.byref(IID_IMMDEVICE_ENUMERATOR),
            ctypes.byref(enumerator),
        ), "CoCreateInstance")

        _check_hresult(
            enumerator.contents.lpVtbl.contents.GetDefaultAudioEndpoint(
                enumerator,
                E_RENDER,
                E_MULTIMEDIA,
                ctypes.byref(device),
            ),
            "GetDefaultAudioEndpoint",
        )

        volume_ptr = ctypes.c_void_p()
        _check_hresult(
            device.contents.lpVtbl.contents.Activate(
                device,
                ctypes.byref(IID_IAUDIO_ENDPOINT_VOLUME),
                CLSCTX_ALL,
                None,
                ctypes.byref(volume_ptr),
            ),
            "Activate IAudioEndpointVolume",
        )
        volume = ctypes.cast(volume_ptr, LP_VOLUME)

        scalar = ctypes.c_float()
        muted = wintypes.BOOL()
        _check_hresult(
            volume.contents.lpVtbl.contents.GetMasterVolumeLevelScalar(
                volume,
                ctypes.byref(scalar),
            ),
            "GetMasterVolumeLevelScalar",
        )
        _check_hresult(
            volume.contents.lpVtbl.contents.GetMute(volume, ctypes.byref(muted)),
            "GetMute",
        )

        level = max(0, min(100, int(round(scalar.value * 100))))
        return VolumeStatus(level, bool(muted.value))
    finally:
        if volume:
            volume.contents.lpVtbl.contents.Release(volume)
        if device:
            device.contents.lpVtbl.contents.Release(device)
        if enumerator:
            enumerator.contents.lpVtbl.contents.Release(enumerator)
        if initialized:
            ole32.CoUninitialize()


async def _get_windows_media_status_with_retry() -> MediaStatus:
    last_status = MediaStatus(title="No session", artist="", status="Stopped")

    for attempt in range(7):
        last_status = await _get_windows_media_status_once()
        if _media_status_has_track(last_status):
            return _cache_media_status(last_status)

        cached = _cached_media_status()
        if cached is not None and attempt >= 2:
            return cached

        await asyncio.sleep(0.2)

    cached = _cached_media_status()
    if cached is not None:
        return cached

    return last_status


async def _get_windows_media_status_once() -> MediaStatus:
    module = _import_media_control_module()
    manager_type = module.GlobalSystemMediaTransportControlsSessionManager
    manager = await manager_type.request_async()
    session = manager.get_current_session()
    if session is None:
        return MediaStatus(title="No session", artist="", status="Stopped")

    properties = await session.try_get_media_properties_async()
    playback_info = session.get_playback_info()
    raw_playback_status = playback_info.playback_status
    if hasattr(raw_playback_status, "name"):
        playback_status = str(raw_playback_status.name)
    elif hasattr(raw_playback_status, "value"):
        playback_status = str(raw_playback_status.value)
    else:
        playback_status = str(raw_playback_status)

    return MediaStatus(
        title=str(getattr(properties, "title", "") or ""),
        artist=str(getattr(properties, "artist", "") or ""),
        status=playback_status,
    )


def _import_media_control_module():
    for module_name in (
        "winsdk.windows.media.control",
        "winrt.windows.media.control",
    ):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue

    raise RuntimeError("install optional package: python -m pip install winsdk")
