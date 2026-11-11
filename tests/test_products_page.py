import unittest
from html.parser import HTMLParser
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
        self.assertNotIn('id="project-shift-calendar"', self.html[music_start:music_end])
        self.assertIn('id="tennko-kakuninnbo"', self.html[transport_start:])
        self.assertIn('id="project-shift-calendar"', self.html[transport_start:])
        self.assertNotIn('class="product-track"', self.html)

    def test_transport_product_explains_features_and_license(self):
        self.assertIn("運送業務向けアプリ", self.html)
        self.assertIn("音楽アプリとは別に", self.html)
        self.assertIn("Excel・CSV形式で月次データを書き出し", self.html)
        self.assertIn("買い切りWebアプリ利用ライセンス", self.html)
        self.assertIn("利用開始ガイド", self.html)

    def test_product_browser_supports_filtering_and_detail_selection(self):
        browser = self.html.split('<section class="product-browser"', 1)[1].split(
            '<section class="catalog"', 1
        )[0]

        self.assertEqual(browser.count('class="product-card"'), 5)
        self.assertIn('aria-live="polite">5件の商品</p>', browser)
        self.assertIn('data-product-filter="music"', browser)
        self.assertIn('data-product-filter="transport"', browser)
        self.assertIn('data-product-filter="free"', browser)
        self.assertIn('data-product-filter="business"', browser)
        self.assertIn('data-product-target="metronome"', browser)
        self.assertIn('data-product-target="tennko-kakuninnbo"', browser)
        self.assertIn('data-product-target="beisia-work-records"', browser)
        self.assertIn('data-product-target="project-shift-calendar"', browser)
        self.assertIn('data-product-target="transport-mechanic"', browser)
        self.assertIn("function showProduct(productId", self.html)
        self.assertIn("function closeProduct()", self.html)

    def test_every_product_detail_is_reachable_from_a_matching_catalog_card(self):
        elements = []

        class ElementParser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                elements.append(dict(attrs))

        ElementParser().feed(self.html)
        details = {
            element["id"]
            for element in elements
            if "product-feature" in element.get("class", "").split()
        }
        buttons = [element for element in elements if "data-product-target" in element]
        self.assertCountEqual(
            [button["data-product-target"] for button in buttons], details
        )
        for button in buttons:
            with self.subTest(product=button["data-product-target"]):
                self.assertEqual(
                    button["aria-controls"],
                    "music-products"
                    if button["data-product-target"] == "metronome"
                    else "transport-products",
                )

    def test_new_catalog_cards_use_existing_categories_and_product_information(self):
        browser = self.html.split('<section class="product-browser"', 1)[1].split(
            '<section class="catalog"', 1
        )[0]
        for product_id, name, price in (
            ("project-shift-calendar", "案件別シフト表", "12,800円"),
            ("transport-mechanic", "自動車整備点検簿", "2,980円"),
        ):
            card = next(
                card
                for card in browser.split('<article class="product-card"')[1:]
                if f'data-product-target="{product_id}"' in card
            ).split("</article>", 1)[0]
            with self.subTest(product=product_id):
                self.assertIn('data-product-categories="transport business"', card)
                self.assertIn(name, card)
                self.assertIn(price, card)
                self.assertNotIn("service-purchase", card)

    def test_future_products_include_transport_apps(self):
        future_products = self.html.split('<section class="future-products"', 1)[1]
        self.assertIn("運送業務アプリ", future_products)
        self.assertIn("日報、点呼、配送実績", future_products)

    def test_transport_apps_use_mobile_visual_previews(self):
        self.assertIn('class="transport-app-preview"', self.html)
        self.assertIn("../data/media/tennko-record-entry.png", self.html)
        self.assertIn("../data/media/beisia-work-records-mobile.png", self.html)
        self.assertNotIn(
            '<iframe class="app-window tenko-window" src="https://tennko-kakuninnbo.onrender.com/"',
            self.html,
        )
        self.assertNotIn(
            '<iframe class="app-window tenko-window" src="https://beisia-work-records.vercel.app/"',
            self.html,
        )
        self.assertIn(".transport-catalog .store-actions { order: -1; }", self.html)

    def test_project_shift_calendar_is_listed_with_sales_admin(self):
        card = self.html.split('id="project-shift-calendar"', 1)[1].split(
            "</article>", 1
        )[0]

        self.assertIn("案件別シフト表", card)
        self.assertIn("https://project-shift-calendar.vercel.app/", card)
        self.assertIn("12,800円", card)
        self.assertIn("税込・買い切り", card)
        self.assertIn('id="project-shift-sales-form"', card)
        self.assertIn('id="project-shift-sales-enabled"', card)
        self.assertIn('class="service-purchase project-shift-purchase-link"', card)
        self.assertIn('class="transport-app-preview project-shift-preview"', card)
        self.assertNotIn("<iframe", card)
        self.assertIn(
            'fetch("../api/store/project-shift-calendar/product"', self.html
        )
        self.assertIn("loadProjectShiftSales();", self.html)

    def test_transport_mechanic_is_listed_with_shared_sales_control(self):
        card = self.html.split('id="transport-mechanic"', 1)[1].split(
            "</article>", 1
        )[0]
        transport_section_start = self.html.index(
            '<section class="transport-catalog" id="transport-products"'
        )
        future_section_start = self.html.index(
            '<section class="future-products"', transport_section_start
        )

        self.assertIn("自動車整備点検簿", card)
        self.assertIn(
            'id="transport-mechanic"',
            self.html[transport_section_start:future_section_start],
        )
        self.assertIn('class="transport-app-preview project-shift-preview"', card)
        self.assertIn("2,980円", card)
        self.assertIn("20台プラン", card)
        self.assertIn("49,800円", card)
        self.assertIn("管理者を含め最大20人", card)
        self.assertIn('id="mechanic-sales-form"', card)
        self.assertIn('id="mechanic-sales-enabled"', card)
        self.assertIn('class="service-purchase mechanic-purchase-link"', card)
        self.assertIn(
            'fetch("../api/store/transport-mechanic-app/product"', self.html
        )
        self.assertIn(
            "requestMechanicSales().then(data => renderMechanicSales(data.enabled))",
            self.html,
        )

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

    def test_transpose_lab_source_is_removed_but_legacy_archive_remains(self):
        root = Path(__file__).resolve().parents[1]

        self.assertFalse((root / "trumpet-transpose-lab").exists())
        self.assertNotIn("Trumpet Transpose Lab", self.html)
        self.assertNotIn("trumpet-transpose-lab", self.html)

        with ZipFile(root / "private" / "products" / "trumpet-transpose-lab.zip") as archive:
            self.assertIsNone(archive.testzip())
            archive_html = archive.read("index.html").decode("utf-8")

        self.assertIn("Trumpet Transpose Lab V2", archive_html)
        self.assertNotIn('href="./styles.css"', archive_html)
        self.assertNotIn('src="./app.mjs"', archive_html)

    def test_legacy_transpose_lab_archive_has_bulk_correction_tools(self):
        root = Path(__file__).resolve().parents[1]
        with ZipFile(root / "private" / "products" / "trumpet-transpose-lab.zip") as archive:
            archive_html = archive.read("index.html").decode("utf-8")

        self.assertIn('id="fixTools"', archive_html)
        self.assertIn('id="noteAddButton"', archive_html)
        self.assertIn('id="noteSplitButton"', archive_html)
        self.assertIn('id="snapAllButton"', archive_html)
        self.assertIn('id="removeShortButton"', archive_html)
        self.assertIn("function addNote()", archive_html)
        self.assertIn("function splitSelectedNote()", archive_html)
        self.assertIn("function snapAllToGrid()", archive_html)
        self.assertIn("function removeShortNotes()", archive_html)

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