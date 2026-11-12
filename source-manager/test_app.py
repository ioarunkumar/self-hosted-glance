import json
import tempfile
import unittest
from pathlib import Path

from app import DuplicateSource, SourceStore


class SourceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.data_path = root / "data" / "sources.json"
        self.config_path = root / "config" / "user-sources.yml"
        self.store = SourceStore(self.data_path, self.config_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_adds_and_persists_website_and_youtube_sources(self):
        channel = "UCXuqSBlHAE6Xw-yeJA0Tunw"
        self.store.add_website("https://example.com/feed.xml", 'Example "News"')
        self.store.add_youtube_channel(f"https://www.youtube.com/channel/{channel}")

        reloaded = SourceStore(self.data_path, self.config_path)
        self.assertEqual(
            reloaded.get_sources(),
            {
                "websites": [{"url": "https://example.com/feed.xml", "title": 'Example "News"'}],
                "youtube_channels": [channel],
            },
        )
        generated = self.config_path.read_text(encoding="utf-8")
        self.assertIn('title: "Example \\"News\\""', generated)
        self.assertIn(f'- "{channel}"', generated)

    def test_rejects_duplicate_sources_and_invalid_urls(self):
        self.store.add_website("https://example.com/feed.xml", "")
        with self.assertRaises(DuplicateSource):
            self.store.add_website("https://example.com/feed.xml", "")
        with self.assertRaises(ValueError):
            self.store.add_website("file:///etc/passwd", "")
        with self.assertRaises(ValueError):
            self.store.add_youtube_channel("https://www.youtube.com/@channel")

    def test_removes_source_and_regenerates_widget_config(self):
        self.store.add_website("https://example.com/feed.xml", "Example")
        self.store.add_youtube_channel("UCXuqSBlHAE6Xw-yeJA0Tunw")

        self.assertTrue(self.store.remove_source("website", "https://example.com/feed.xml"))
        self.assertFalse(self.store.remove_source("website", "https://example.com/feed.xml"))
        self.assertEqual(self.store.get_sources()["websites"], [])
        self.assertNotIn("- type: rss", self.config_path.read_text(encoding="utf-8"))

    def test_rejects_corrupted_persisted_data(self):
        self.data_path.write_text(json.dumps({"websites": "not a list", "youtube_channels": []}))
        with self.assertRaises(ValueError):
            SourceStore(self.data_path, self.config_path)


if __name__ == "__main__":
    unittest.main()
