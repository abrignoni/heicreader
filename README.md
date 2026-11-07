# heicreader

heicreader opens HEIC and HEIF images, the format iPhones save photos in, and hands back the
pixels and the Exif block. The LEAPPs use it to show HEIC media in their reports as JPEG or PNG.

It is a small Python package around a helper program written in Go. The helper uses
[gen2brain/h265](https://github.com/gen2brain/h265), a pure-Go HEVC decoder under the MIT
licence. Nothing in it is GPL or LGPL, so a program that bundles it can keep its own licence.
The licence texts of what is built into the helper are in [LICENSES](LICENSES/THIRD-PARTY-NOTICES.md).

## Use

```python
import heicreader

image = heicreader.to_pil("IMG_0001.HEIC")       # a Pillow image (Pillow must be installed)
decoded = heicreader.decode(path_or_bytes)       # mode ("RGB" or "RGBA"), width, height, pixels
exif = heicreader.raw_exif(path_or_bytes)        # the Exif block as stored, or None
```

A decode applies the rotation, mirroring and crop the file asks for, so the image comes out
the way it is meant to be shown. `raw_exif` returns the Exif block as stored in the file. The
Orientation tag is left as the file has it, it is not rewritten to 1.

The helper starts on the first call and stays running, so reading a thousand images costs one
program start. A damaged file raises `heicreader.HeicReaderError` and the next call carries on.
A decode that takes longer than its `timeout` (120 seconds by default) stops the helper and
raises; the next call starts a new one.

From the command line, the helper itself also converts single files:

```bash
heicreader convert IMG_0001.HEIC IMG_0001.jpg
heicreader exif IMG_0001.HEIC IMG_0001.exif
```

## Install

Each release has one wheel per platform: Windows x64 and ARM, macOS Apple silicon and Intel,
and Linux x64 and ARM. A wheel works on any Python 3.9 or newer. Install the one for your
machine from the releases page, or list them in a requirements file with platform markers.

## What it was checked against

Before the first release the helper was compared with pillow-heif 1.8.1 on 1,178 HEIC files
from two test extractions: 917 cached images and 11 photos from one, and 250 wallpaper layers
from the other.

- All 1,178 decoded, at the same size and orientation as pillow-heif.
- Brightness matches closely. Colour can differ slightly at sharp edges, because the two
  rebuild the half-resolution colour planes differently. On the 74 files where they differed
  most, the helper was closer to macOS's own decoder than pillow-heif on 71.
- Transparency matched exactly on all 67 wallpaper layers that have it.
- Exif matched tag for tag, except Orientation: pillow-heif reports 1 after rotating, and
  heicreader reports the value stored in the file.

Those are measurements on those files, not a guarantee for every HEIC.

## Patents

HEVC is covered by patents. The licences here cover copyright only and give no rights under
those patents.

## Building

```bash
python tools/build_wheels.py --local     # the helper for this machine, for the tests
python -m unittest discover -s tests
python tools/build_wheels.py             # all six wheels into dist/
```

Needs Go 1.26 or newer. The decoder comes from abrignoni/h265, a fork of gen2brain/h265 that
carries one fix (alpha stored as a grid of its own, as Apple writes it), offered upstream as
gen2brain/h265#12.
