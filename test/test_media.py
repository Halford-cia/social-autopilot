import unittest
from io import BytesIO

from PIL import Image

from social.media import FLAGS, candidate_url, sanitize


def source():
    f = BytesIO()
    Image.new("RGB", (800, 600)).save(f, "JPEG")
    return f.getvalue()


class Privacy(unittest.TestCase):
    def test_requires_both_tags_and_safe_metadata(self):
        photo = dict(
            tags=[{"label": "After"}, " drone "],
            uris=[{"type": "original", "uri": "https://cdn.companycam.com/photo.jpg"}],
        )
        self.assertTrue(candidate_url(photo))
        for bad in [
            dict(tags=["after"]),
            dict(description="Street sign in frame"),
            dict(coordinates={"lat": 1}),
            dict(tags=["after", "drone", "face"]),
            dict(description="House 62"),
        ]:
            self.assertIsNone(candidate_url(photo | bad))

    def test_explicit_all_clear_only(self):
        clear = dict.fromkeys(FLAGS, False)
        for answer in [
            None,
            {},
            clear | {"face": True},
            clear | {"uncertain": True},
            clear | {"street_sign": "false"},
        ]:
            self.assertIsNone(sanitize(source(), "acacia", lambda *_, answer=answer: answer))
        result = sanitize(source(), "acacia", lambda *_: clear)
        with Image.open(BytesIO(result)) as output:
            self.assertEqual(output.format, "JPEG")
            self.assertFalse(output.getexif())

    def test_embedded_metadata_overrides_visual_clear(self):
        exif = Image.Exif()
        exif[270] = "House number visible"
        out = BytesIO()
        Image.new("RGB", (800, 600)).save(out, "JPEG", exif=exif)
        self.assertIsNone(sanitize(out.getvalue(), "acacia", lambda *_: dict.fromkeys(FLAGS, False)))
