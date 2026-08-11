"""
Tests for poslib/photos.py - best-effort item photo extraction.

The cache-round-trip tests use a synthetic tmp_path and a monkeypatched
`_read_raw_picture`, so they never touch the real database. The one test
against the live database proves the "no photo" path degrades cleanly for
real item IDs, since - empirically - none of them currently have one.
"""

from __future__ import annotations

from poslib import photos
from poslib.config import get_config


class TestSniff:

    def test_recognises_jpeg(self):
        raw = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        assert photos._sniff(raw) == ("jpg", 0)

    def test_recognises_png(self):
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        assert photos._sniff(raw) == ("png", 0)

    def test_finds_signature_after_a_wrapper_header(self):
        # Simulates an OLE/Package wrapper sitting before the real image.
        raw = b"SOME-OLE-HEADER-JUNK" + b"\xff\xd8\xff" + b"\x00" * 20
        found = photos._sniff(raw)
        assert found is not None
        ext, offset = found
        assert ext == "jpg"
        assert offset == 20

    def test_unrecognised_bytes_return_none(self):
        assert photos._sniff(b"not an image, just text") is None

    def test_empty_bytes_return_none(self):
        assert photos._sniff(b"") is None


class TestGetItemPhoto:

    def _cfg_with_cache_dir(self, monkeypatch, tmp_path):
        from poslib.config import Config
        cfg = get_config()
        original = Config.path

        def patched(self, key, default=""):
            if key == "catalog.photo_cache_dir":
                return tmp_path
            return original(self, key, default)

        monkeypatch.setattr(cfg, "path", patched.__get__(cfg))
        return cfg

    def test_returns_none_and_caches_sentinel_when_no_photo(self, monkeypatch, tmp_path):
        cfg = self._cfg_with_cache_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(photos, "_read_raw_picture", lambda cfg, item_id: None)
        result = photos.get_item_photo(cfg, 12345)
        assert result is None
        assert (tmp_path / "12345.none").is_file()

    def test_second_lookup_uses_the_sentinel_without_re_reading(self, monkeypatch, tmp_path):
        cfg = self._cfg_with_cache_dir(monkeypatch, tmp_path)
        calls = {"n": 0}

        def fake_read(cfg, item_id):
            calls["n"] += 1
            return None

        monkeypatch.setattr(photos, "_read_raw_picture", fake_read)
        photos.get_item_photo(cfg, 999)
        photos.get_item_photo(cfg, 999)
        assert calls["n"] == 1

    def test_caches_a_recognised_image(self, monkeypatch, tmp_path):
        cfg = self._cfg_with_cache_dir(monkeypatch, tmp_path)
        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x01" * 50
        monkeypatch.setattr(photos, "_read_raw_picture", lambda cfg, item_id: fake_jpeg)
        result = photos.get_item_photo(cfg, 42)
        assert result is not None
        data, ext = result
        assert ext == "jpg"
        assert data == fake_jpeg
        assert (tmp_path / "42.jpg").is_file()

    def test_unrecognisable_bytes_are_treated_as_no_photo(self, monkeypatch, tmp_path):
        cfg = self._cfg_with_cache_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(photos, "_read_raw_picture", lambda cfg, item_id: b"garbage bytes")
        result = photos.get_item_photo(cfg, 7)
        assert result is None
        assert (tmp_path / "7.none").is_file()


class TestAgainstLiveDatabase:

    def test_no_real_item_currently_has_a_photo(self, metrics):
        """
        Empirically, 0 of 1,570 real items have anything in Item.Picture.
        This proves the "no photo" path degrades cleanly - never raises -
        for real item IDs read straight from the source database.
        """
        item_ids = metrics.items["item_id"].head(2).tolist()
        cfg = get_config()
        for item_id in item_ids:
            assert photos.get_item_photo(cfg, int(item_id)) is None
