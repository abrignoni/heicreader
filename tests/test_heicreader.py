"""heicreader against synthetic fixtures, through the helper binary in heicreader/bin.

tests/data holds three synthetic images, made for these tests:
  plain.heic        200x120, a red block at x 10-59, y 10-29 (saved by macOS sips)
  orientation.heic  the same picture with Exif Orientation 6 and the matching rotation,
                    so it decodes as 120x200 while its Exif still says 6
  grid_alpha.heic   1100x700, alpha stored as a grid of its own, the way macOS writes it:
                    opaque inside an ellipse, transparent outside, half transparent in
                    the first 40 columns (saved by sips)
Build the helper first: python tools/build_wheels.py --local
"""
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import heicreader  # noqa: E402  pylint: disable=wrong-import-position

DATA = ROOT / "tests" / "data"


@unittest.skipUnless(heicreader.available(), "build the helper first: python tools/build_wheels.py --local")
class HelperTests(unittest.TestCase):

    def test_a_plain_image_decodes_to_rgb_with_its_colours(self):
        decoded = heicreader.decode(DATA / "plain.heic")
        self.assertEqual((decoded.mode, decoded.width, decoded.height), ("RGB", 200, 120))
        self.assertEqual(len(decoded.pixels), 200 * 120 * 3)
        offset = (20 * 200 + 15) * 3
        red, green, blue = decoded.pixels[offset:offset + 3]
        self.assertGreater(red, 230)
        self.assertLess(max(green, blue), 30)

    def test_bytes_and_paths_give_the_same_pixels(self):
        path = DATA / "plain.heic"
        self.assertEqual(heicreader.decode(path), heicreader.decode(path.read_bytes()))
        self.assertEqual(heicreader.decode(path), heicreader.decode(str(path)))

    def test_a_grid_whose_alpha_is_a_grid_keeps_its_alpha(self):
        decoded = heicreader.decode(DATA / "grid_alpha.heic")
        self.assertEqual((decoded.mode, decoded.width, decoded.height), ("RGBA", 1100, 700))

        def alpha(x, y):
            return decoded.pixels[(y * decoded.width + x) * 4 + 3]

        self.assertGreaterEqual(alpha(550, 350), 250)
        self.assertLessEqual(alpha(150, 20), 5)
        self.assertTrue(115 <= alpha(10, 350) <= 140, alpha(10, 350))

    def test_rotation_is_applied_and_the_stored_exif_is_left_alone(self):
        decoded = heicreader.decode(DATA / "orientation.heic")
        self.assertEqual((decoded.width, decoded.height), (120, 200))
        exif = heicreader.raw_exif(DATA / "orientation.heic")
        self.assertIsNotNone(exif)
        self.assertTrue(exif.startswith((b"MM\x00*", b"II*\x00")), exif[:4])
        try:
            from PIL import Image  # pylint: disable=import-outside-toplevel
        except ImportError:
            return
        tags = Image.Exif()
        tags.load(exif)
        self.assertEqual(tags.get(274), 6)
        self.assertEqual(tags.get(271), "heicreader test")

    def test_an_image_without_exif_gives_none(self):
        self.assertIsNone(heicreader.raw_exif(DATA / "plain.heic"))

    def test_to_pil(self):
        try:
            import PIL  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
        except ImportError:
            self.skipTest("Pillow is not installed")
        image = heicreader.to_pil(DATA / "grid_alpha.heic")
        self.assertEqual((image.mode, image.size), ("RGBA", (1100, 700)))

    def test_damaged_input_raises_and_the_helper_keeps_working(self):
        data = (DATA / "plain.heic").read_bytes()
        for bad in (data[:200], b"not an image at all", b""):
            with self.assertRaises(heicreader.HeicReaderError):
                heicreader.decode(bad)
        self.assertEqual(heicreader.decode(DATA / "plain.heic").width, 200)

    def test_a_timeout_kills_the_helper_and_the_next_call_starts_a_new_one(self):
        # A stand-in helper that never answers, so the timeout is what ends the request.
        helper = heicreader._Helper()  # pylint: disable=protected-access
        silent = [sys.executable, "-c", "import time; time.sleep(60)"]
        with mock.patch.object(helper, "_command", return_value=silent):
            with self.assertRaises(heicreader.HeicReaderError) as caught:
                helper.request("decode", (DATA / "plain.heic").read_bytes(), 0.5)
        self.assertIn("timed out", str(caught.exception))
        self.assertIsNone(helper._proc)  # pylint: disable=protected-access
        answer, body = helper.request("decode", (DATA / "plain.heic").read_bytes(), 30)
        self.assertEqual((answer["w"], len(body)), (200, 200 * 120 * 3))
        helper.close()

    def test_the_helper_reports_a_version(self):
        self.assertTrue(heicreader.helper_version())

    def test_close_then_use_again(self):
        heicreader.close()
        self.assertEqual(heicreader.decode(DATA / "plain.heic").height, 120)


class NoHelperTests(unittest.TestCase):

    def test_no_binary_raises_unavailable(self):
        helper = heicreader._Helper()  # pylint: disable=protected-access
        with mock.patch.object(heicreader, "binary_path", return_value=None):
            with self.assertRaises(heicreader.Unavailable):
                helper.request("version", b"", 5)

    def test_unavailable_is_a_heicreader_error(self):
        self.assertTrue(issubclass(heicreader.Unavailable, heicreader.HeicReaderError))


if __name__ == "__main__":
    unittest.main()
