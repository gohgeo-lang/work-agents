import io
import os
import unittest


os.environ.setdefault("LFL_SKIP_CONFIG_VALIDATE", "1")

try:
    from app import normalize_ref, parse_theme, parse_shorts_topic_lines, sniff_image_type
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"Missing dependency for app import: {exc.name}") from exc


class TestUtils(unittest.TestCase):
    def test_normalize_ref_korean_spacing(self):
        self.assertEqual(normalize_ref("히브리서11:1"), "히브리서 11:1")

    def test_normalize_ref_english_spacing(self):
        self.assertEqual(normalize_ref("Hebrews11:1"), "히브리서 11:1")

    def test_parse_theme_colon(self):
        self.assertEqual(parse_theme("Faith:믿음"), ("Faith", "믿음"))

    def test_parse_theme_slash(self):
        self.assertEqual(parse_theme("Hope/소망"), ("Hope", "소망"))

    def test_parse_shorts_topic_lines_numbered(self):
        text = "1) 첫 번째\n2. 두 번째\n"
        self.assertEqual(parse_shorts_topic_lines(text), ["첫 번째", "두 번째"])

    def test_sniff_image_type_png(self):
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        stream = io.BytesIO(data)
        self.assertEqual(sniff_image_type(stream), "png")

    def test_sniff_image_type_jpeg(self):
        data = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        stream = io.BytesIO(data)
        self.assertEqual(sniff_image_type(stream), "jpeg")

    def test_sniff_image_type_webp(self):
        data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
        stream = io.BytesIO(data)
        self.assertEqual(sniff_image_type(stream), "webp")


if __name__ == "__main__":
    unittest.main()
