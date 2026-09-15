import unittest
from pathlib import Path


class BeisiaSalesAdminTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (Path(__file__).parents[1] / "products" / "index.html").read_text(
            encoding="utf-8"
        )

    def test_beisia_card_is_listed_as_transport_product(self):
        transport = self.page.split('<section class="transport-catalog"', 1)[1]
        self.assertIn('id="beisia-work-records"', transport)
        self.assertIn("自在稼働記録", transport)
        self.assertIn("https://beisia-work-records.vercel.app/", transport)
        self.assertIn("400円", transport)
        self.assertIn("8,000円", transport)

    def test_beisia_card_has_sales_admin_controls(self):
        self.assertIn('id="beisia-sales-form"', self.page)
        self.assertIn('id="beisia-sales-password"', self.page)
        self.assertIn('id="beisia-sales-enabled"', self.page)
        self.assertIn('id="beisia-sales-save"', self.page)

    def test_beisia_sales_update_uses_protected_store_endpoint(self):
        self.assertIn('fetch("../api/store/beisia-work-records/product"', self.page)
        self.assertIn("requestBeisiaSales({", self.page)
        self.assertIn('"X-Editor-Password": password', self.page)
        self.assertIn("loadBeisiaSales();", self.page)


if __name__ == "__main__":
    unittest.main()