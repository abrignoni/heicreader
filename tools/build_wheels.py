"""Build the heicreader helper and pack it into one wheel per platform.

    python tools/build_wheels.py            all six platforms -> dist/*.whl
    python tools/build_wheels.py --local    this machine only -> heicreader/bin/, for tests

The helper is pure Go (CGO_ENABLED=0), so every platform is cross-compiled from one
machine and the Linux binary is static, which is why one Linux wheel carries both the
manylinux and the musllinux tags. The wheels are py3-none: the package is pure Python
around a standalone executable, so one wheel serves every Python version. The macOS tag
is read from the minimum OS version written into the built binary, not assumed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "heicreader"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
NAME = "heicreader"

# (GOOS, GOARCH, wheel platform tag or None for macOS, read from the binary)
TARGETS = [
    ("darwin", "arm64", None),
    ("darwin", "amd64", None),
    ("linux", "amd64", "manylinux_2_17_x86_64.manylinux2014_x86_64.musllinux_1_1_x86_64"),
    ("linux", "arm64", "manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64"),
    ("windows", "amd64", "win_amd64"),
    ("windows", "arm64", "win_arm64"),
]
MACOS_ARCH = {"arm64": "arm64", "amd64": "x86_64"}
# A fixed timestamp makes the same inputs give the same wheel bytes.
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def version() -> str:
    text = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)


def go() -> str:
    found = shutil.which("go")
    if not found:
        sys.exit("build_wheels: go is not on PATH")
    return found


def build_binary(goos: str, goarch: str, out: Path, ver: str) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CGO_ENABLED="0", GOOS=goos, GOARCH=goarch)
    cmd = [go(), "build", "-trimpath", "-buildvcs=false",
           "-ldflags", f"-s -w -X main.version={ver}", "-o", str(out), "."]
    print("==>", goos, goarch, " ".join(cmd[1:]), flush=True)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True, stdin=subprocess.DEVNULL)
    if not out.is_file() or out.stat().st_size == 0:
        sys.exit(f"build_wheels: {out} was not produced")
    return out


def macos_minimum(binary: Path) -> tuple[int, int]:
    """The minimum macOS version in a 64-bit Mach-O's LC_BUILD_VERSION or LC_VERSION_MIN_MACOSX."""
    data = binary.read_bytes()
    magic, = struct.unpack_from("<I", data, 0)
    if magic != 0xFEEDFACF:
        sys.exit(f"build_wheels: {binary} is not a 64-bit little-endian Mach-O")
    ncmds, = struct.unpack_from("<I", data, 16)
    offset = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, offset)
        if cmd == 0x32:      # LC_BUILD_VERSION: platform, minos, sdk
            minos, = struct.unpack_from("<I", data, offset + 12)
            return minos >> 16, (minos >> 8) & 0xFF
        if cmd == 0x24:      # LC_VERSION_MIN_MACOSX: version, sdk
            minos, = struct.unpack_from("<I", data, offset + 8)
            return minos >> 16, (minos >> 8) & 0xFF
        offset += size
    sys.exit(f"build_wheels: no minimum macOS version in {binary}")


def _record_line(arcname: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{arcname},sha256={digest},{len(data)}"


def metadata(ver: str, licence_files: list[str]) -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {NAME}",
        f"Version: {ver}",
        "Summary: Read HEIC and HEIF images with a bundled pure-Go decoder, no GPL or LGPL code",
        "Author: Alexis Brignoni",
        "License-Expression: MIT AND BSD-3-Clause",
        *[f"License-File: {name}" for name in licence_files],
        "Project-URL: Source, https://github.com/abrignoni/heicreader",
        "Requires-Python: >=3.9",
        "Classifier: Programming Language :: Python :: 3",
        "Classifier: Topic :: Multimedia :: Graphics :: Graphics Conversion",
        "Description-Content-Type: text/markdown",
        "",
        readme,
    ]
    return "\n".join(lines)


def licence_sources() -> list[tuple[str, Path]]:
    """(name inside dist-info/licenses, source file): the project's own and those it builds in."""
    files = [("LICENSE", ROOT / "LICENSE")]
    files += [(f"LICENSES/{p.name}", p) for p in sorted((ROOT / "LICENSES").iterdir()) if p.is_file()]
    return files


def pack_wheel(binary: Path, tag: str, ver: str, exe_name: str) -> Path:
    DIST.mkdir(exist_ok=True)
    dist_info = f"{NAME}-{ver}.dist-info"
    wheel_name = f"{NAME}-{ver}-py3-none-{tag}.whl"
    path = DIST / wheel_name
    record = []

    def add(zf: zipfile.ZipFile, arcname: str, data: bytes, executable: bool = False):
        info = zipfile.ZipInfo(arcname, ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100755 if executable else 0o100644) << 16
        zf.writestr(info, data)
        record.append(_record_line(arcname, data))

    licences = licence_sources()
    with zipfile.ZipFile(path, "w") as zf:
        add(zf, f"{NAME}/__init__.py", (PACKAGE / "__init__.py").read_bytes())
        add(zf, f"{NAME}/bin/{exe_name}", binary.read_bytes(), executable=True)
        for name, source in licences:
            add(zf, f"{dist_info}/licenses/{name}", source.read_bytes())
        add(zf, f"{dist_info}/METADATA", metadata(ver, [n for n, _ in licences]).encode("utf-8"))
        wheel = ("Wheel-Version: 1.0\nGenerator: heicreader tools/build_wheels.py\n"
                 "Root-Is-Purelib: false\n" + "".join(f"Tag: py3-none-{t}\n" for t in tag.split(".")))
        add(zf, f"{dist_info}/WHEEL", wheel.encode("ascii"))
        record.append(f"{dist_info}/RECORD,,")
        info = zipfile.ZipInfo(f"{dist_info}/RECORD", ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        zf.writestr(info, "\n".join(record) + "\n")
    print(f"==> {path.name}: {path.stat().st_size:,} bytes", flush=True)
    return path


def local() -> Path:
    goos = {"darwin": "darwin", "linux": "linux", "win32": "windows"}[sys.platform]
    machine = platform.machine().lower()
    goarch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}[machine]
    exe = NAME + (".exe" if goos == "windows" else "")
    return build_binary(goos, goarch, PACKAGE / "bin" / exe, version())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", action="store_true", help="build for this machine into heicreader/bin")
    args = parser.parse_args(argv)
    if args.local:
        print(local())
        return 0
    ver = version()
    if DIST.exists():
        shutil.rmtree(DIST)
    for goos, goarch, tag in TARGETS:
        exe = NAME + (".exe" if goos == "windows" else "")
        binary = build_binary(goos, goarch, BUILD / f"{goos}-{goarch}" / exe, ver)
        if tag is None:
            major, minor = macos_minimum(binary)
            tag = f"macosx_{major}_{minor}_{MACOS_ARCH[goarch]}"
        pack_wheel(binary, tag, ver, exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
