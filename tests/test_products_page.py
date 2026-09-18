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

    def test_future_products_include_transport_apps(self):
        future_products = self.html.split('<section class="future-products"', 1)[1]
        self.assertIn("運送業務アプリ", future_products)
        self.assertIn("日報、点呼、配送実績", future_products)

    def test_transport_apps_use_mobile_visual_previews(self):
        self.assertIn('class="mobile-app-preview"', self.html)
        self.assertIn("../data/media/tennko-record-entry.png", self.html)
        self.assertIn("../data/media/beisia-work-records-mobile.png", self.html)
        self.assertIn(".transport-catalog .app-window { display: none; }", self.html)
        self.assertIn(".transport-catalog .store-actions { order: -1; }", self.html)

    def test_hero_represents_music_and_one_transport_app(self):
        hero = self.html.split('<section class="hero">', 1)[1].split("</section>", 1)[0]
        hero_image = Path(__file__).resolve().parents[1] / "data" / "media" / "products-operations-hero.png"

        self.assertIn("音楽・運送の現場を", hero)
        self.assertIn("メトロノーム", hero)
        self.assertIn("自在稼働記録", hero)
        self.assertIn("../data/media/products-operations-hero.png", hero)
        self.assertNotIn("Transpose", hero)
        self.assertNotIn("点呼確認簿", hero)
        self.assertTrue(hero_image.is_file())

    def test_transpose_lab_uses_only_the_v2_source(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "trumpet-transpose-lab" / "index.html").read_bytes()
        source_html = source.decode("utf-8")

        self.assertIn("Trumpet Transpose Lab V2", source_html)
        self.assertNotIn("Trumpet Transpose Lab", self.html)
        self.assertNotIn("trumpet-transpose-lab", self.html)

        with ZipFile(root / "private" / "products" / "trumpet-transpose-lab.zip") as archive:
            archive_html = archive.read("index.html").decode("utf-8")

        self.assertIn("Trumpet Transpose Lab V2", archive_html)
        self.assertNotIn('href="./styles.css"', archive_html)
        self.assertNotIn('src="./app.mjs"', archive_html)

    def test_transpose_lab_has_bulk_correction_tools(self):
        root = Path(__file__).resolve().parents[1]
        source_html = (root / "trumpet-transpose-lab" / "index.html").read_text(
            encoding="utf-8"
        )
        source_js = (root / "trumpet-transpose-lab" / "app.mjs").read_text(
            encoding="utf-8"
        )

        self.assertIn('id="fixTools"', source_html)
        self.assertIn('id="noteAddButton"', source_html)
        self.assertIn('id="noteSplitButton"', source_html)
        self.assertIn('id="snapAllButton"', source_html)
        self.assertIn('id="removeShortButton"', source_html)
        self.assertIn('id="noteDuration"', source_html)
        self.assertIn('data-edit="duration-down"', source_html)
        self.assertIn('data-edit="duration-up"', source_html)
        self.assertIn('data-edit="delete"', source_html)
        self.assertIn("function addNote()", source_js)
        self.assertIn("function splitSelectedNote()", source_js)
        self.assertIn("function snapAllToGrid()", source_js)
        self.assertIn("function removeShortNotes()", source_js)
        self.assertIn("action === 'duration-down'", source_js)
        self.assertIn("action === 'duration-up'", source_js)
        self.assertIn("$('fixTools').disabled", source_js)
        self.assertIn("$('noteAddButton').addEventListener", source_js)
        self.assertIn("$('noteSplitButton').addEventListener", source_js)
        self.assertIn("$('snapAllButton').addEventListener", source_js)
        self.assertIn("$('removeShortButton').addEventListener", source_js)

    def test_breath_metronome_uses_count_in_and_stops_after_one_cycle(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "music App" / "index.html").read_text(encoding="utf-8")

        self.assertIn("let breathCountInRemaining = 0;", source)
        self.assertIn("breathCountInRemaining = 4;", source)
        self.assertIn("予備カウント ${currentCount}/4", source)
        self.assertIn("ブレス練習を1サイクル完了しました", source)
        self.assertIn("再開すると予備カウントから始まります", source)


if __name__ == "__main__":
    unittest.main()