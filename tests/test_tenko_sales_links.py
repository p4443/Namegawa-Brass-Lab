import unittest
from pathlib import Path


class TenkoSalesLinksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (Path(__file__).parents[1] / "products" / "index.html").read_text(
            encoding="utf-8"
        )

    def test_tenko_product_card_links_both_purchase_plans(self):
        self.assertIn('id="tenko-checkbook"', self.page)
        self.assertIn(
            'class="app-window tenko-window" '
            'src="https://tennko-kakuninnbo.onrender.com/"',
            self.page,
        )
        self.assertIn(
            'href="https://tennko-kakuninnbo.onrender.com/purchase/personal"',
            self.page,
        )
        self.assertIn(
            'href="https://tennko-kakuninnbo.onrender.com/purchase/company"',
            self.page,
        )

    def test_tenko_product_card_links_legal_information(self):
        for path in ("legal-notice", "terms", "privacy"):
            self.assertIn(
                f'href="https://tennko-kakuninnbo.onrender.com/{path}"',
                self.page,
            )

    def test_tenko_purchase_links_do_not_use_download_store_api(self):
        card = self.page.split('id="tenko-checkbook"', 1)[1].split("</article>", 1)[0]
        self.assertNotIn("/api/store/", card)
        self.assertNotIn("download-link", card)


if __name__ == "__main__":
    unittest.main()