import unittest
from pathlib import Path


class TennkoSalesAdminTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = (Path(__file__).parents[1] / "products" / "index.html").read_text(
            encoding="utf-8"
        )

    def test_tennko_card_has_sales_admin_controls(self):
        self.assertIn('id="tenko-sales-form"', self.page)
        self.assertIn('id="tenko-sales-password"', self.page)
        self.assertIn('id="tenko-sales-enabled"', self.page)
        self.assertIn('id="tenko-sales-save"', self.page)

    def test_tennko_sales_update_uses_protected_remote_endpoint(self):
        self.assertIn('"https://tennko-kakuninnbo.onrender.com/api/sales/status"', self.page)
        self.assertIn('method: "PUT"', self.page)
        self.assertIn('"X-Editor-Password": password', self.page)
        self.assertIn('body: JSON.stringify({ enabled: requestedState })', self.page)

    def test_tennko_purchase_buttons_remain_fail_closed(self):
        self.assertIn('link.setAttribute("aria-disabled", "true")', self.page)
        self.assertIn('link.tabIndex = -1', self.page)


if __name__ == "__main__":
    unittest.main()