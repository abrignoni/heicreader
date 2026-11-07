"""Read HEIC and HEIF images through the heicreader helper that ships in this package.

The helper is a small Go program built on a pure-Go HEVC decoder, so nothing here is
under the GPL or the LGPL. It is started once and kept running, so reading many images
costs one process start, not one per image.

    import heicreader
    image = heicreader.to_pil('IMG_0001.HEIC')          # a Pillow image, or raises
    decoded = heicreader.decode(data_or_path)            # mode, width, height, pixels
    exif = heicreader.raw_exif(data_or_path)             # the stored Exif block, or None

A decode applies the file's clean aperture, rotation and mirroring, so the image comes
out the way it is meant to be shown. raw_exif returns the Exif block as stored, Orientation
tag included, without rewriting anything.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import NamedTuple, Union

__version__ = "0.1.0"

Source = Union[str, bytes, bytearray, memoryview, os.PathLike]

DEFAULT_TIMEOUT = 120.0


class HeicReaderError(Exception):
    """The helper could not read the image."""


class Unavailable(HeicReaderError):
    """No helper binary is installed for this platform."""


class Decoded(NamedTuple):
    mode: str        # "RGB" or "RGBA", 8 bits per sample
    width: int
    height: int
    pixels: bytes    # row by row, no padding


def binary_path() -> Path | None:
    """The helper inside this package, or None when the platform has none.

    A frozen program built with PyInstaller keeps the same layout when the binary is
    added under heicreader/bin, so one rule finds it in both cases.
    """
    name = "heicreader.exe" if os.name == "nt" else "heicreader"
    path = Path(__file__).resolve().parent / "bin" / name
    return path if path.is_file() else None


def available() -> bool:
    return binary_path() is not None


def _read_exact(stream, size: int) -> bytes:
    chunks, left = [], size
    while left:
        chunk = stream.read(left)
        if not chunk:
            raise HeicReaderError("the helper stopped mid-answer")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


class _Helper:
    """One running helper, used by one request at a time."""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()

    def _start(self):
        exe = binary_path()
        if exe is None:
            raise Unavailable("no heicreader helper is installed for this platform")
        kwargs = {}
        if os.name == "nt":
            # A windowed program would otherwise flash a console for the helper.
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            [str(exe), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, **kwargs)

    @staticmethod
    def _end(proc, kill: bool):
        """Stop a helper and close both of its pipes."""
        try:
            if kill:
                proc.kill()
            else:
                proc.stdin.close()
            proc.wait(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            try:
                proc.kill()
                proc.wait()
            except OSError:
                pass
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass

    def close(self):
        proc, self._proc = self._proc, None
        if proc is not None:
            self._end(proc, kill=False)

    def request(self, op: str, data: bytes, timeout: float) -> tuple[dict, bytes]:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            proc = self._proc
            # A damaged file must not hang the caller: past the timeout the helper is
            # killed, the read below ends, and the next request starts a new one.
            timer = threading.Timer(timeout, proc.kill)
            timer.daemon = True
            timer.start()
            try:
                proc.stdin.write(json.dumps({"op": op, "len": len(data)}).encode() + b"\n")
                proc.stdin.write(data)
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    raise HeicReaderError("the helper stopped")
                answer = json.loads(line)
                body = _read_exact(proc.stdout, int(answer.get("len", 0)))
            except (OSError, ValueError, HeicReaderError) as ex:
                self._proc = None
                self._end(proc, kill=True)
                timed_out = not timer.is_alive()
                raise HeicReaderError(f"timed out after {timeout:g}s" if timed_out else str(ex)) from ex
            finally:
                timer.cancel()
            if not answer.get("ok"):
                raise HeicReaderError(answer.get("error") or "the helper refused the request")
            return answer, body


_helper = _Helper()
atexit.register(_helper.close)


def _data(source: Source) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    # Read here, not in the helper, so a path in any encoding or length works the same.
    with open(source, "rb") as handle:
        return handle.read()


def decode(source: Source, timeout: float = DEFAULT_TIMEOUT) -> Decoded:
    """Decode a HEIC or HEIF image from a path or its bytes."""
    answer, body = _helper.request("decode", _data(source), timeout)
    return Decoded(answer["mode"], answer["w"], answer["h"], body)


def to_pil(source: Source, timeout: float = DEFAULT_TIMEOUT):
    """Decode into a Pillow image (Pillow must be installed)."""
    from PIL import Image  # pylint: disable=import-outside-toplevel
    decoded = decode(source, timeout)
    return Image.frombytes(decoded.mode, (decoded.width, decoded.height), decoded.pixels)


def raw_exif(source: Source, timeout: float = DEFAULT_TIMEOUT) -> bytes | None:
    """The Exif TIFF block as stored in the file, or None when it has none."""
    _answer, body = _helper.request("exif", _data(source), timeout)
    return body or None


def helper_version(timeout: float = 30.0) -> str:
    """The version the running helper reports."""
    _answer, body = _helper.request("version", b"", timeout)
    return body.decode("ascii", "replace")


def close() -> None:
    """Stop the helper. The next call starts a new one."""
    _helper.close()


if __name__ == "__main__":  # pragma: no cover
    print(binary_path() or "no helper installed", file=sys.stderr)
