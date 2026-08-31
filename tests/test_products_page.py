import unittest
from pathlib import Path
from zipfile import ZipFile


class ProductsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).resolve().parents[1] / "products" / "index.html").read_text(
            encoding="utf-8"
        )

    def test_transport_product_is_separate_from_music_carousel(self):
        music_start = self.html.index('<div class="music-product-track"')
        music_end = self.html.index("\n        </div>\n      </div>\n    </section>", music_start)
        transport_start = self.html.index('<section class="transport-catalog"')

        self.assertLess(music_end, transport_start)
        self.assertNotIn('id="tennko-kakuninnbo"', self.html[music_start:music_end])
        self.assertIn('id="tennko-kakuninnbo"', self.html[transport_start:])
        self.assertNotIn('class="product-track"', self.html)

    def test_transport_product_explains_features_and_license(self):
        self.assertIn("運送業務向けアプリ", self.html)
        self.assertIn("音楽アプリとは別に", self.html)
        self.assertIn("Excel・CSV形式で月次データを書き出し", self.html)
        self.assertIn("買い切りWebアプリ利用ライセンス", self.html)
        self.assertIn("利用開始ガイド", self.html)

    def test_transpose_lab_uses_only_the_v2_source(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "trumpet-transpose-lab" / "index.html").read_bytes()
        source_html = source.decode("utf-8")

        self.assertIn("Trumpet Transpose Lab V2", source_html)
        self.assertIn("Trumpet Transpose Lab V2", self.html)
        self.assertIn("version=2", self.html)

        with ZipFile(root / "private" / "products" / "trumpet-transpose-lab.zip") as archive:
            archive_html = archive.read("index.html").decode("utf-8")

        self.assertIn("Trumpet Transpose Lab V2", archive_html)
        self.assertNotIn('href="./styles.css"', archive_html)
        self.assertNotIn('src="./app.mjs"', archive_html)


if __name__ == "__main__":
    unittest.main()