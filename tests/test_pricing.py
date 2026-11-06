import unittest

from app.billing.pricing import invoice_total


class PricingTests(unittest.TestCase):
    def test_invoice_total(self):
        self.assertEqual(invoice_total(250, 4), 1000)

    def test_zero_quantity(self):
        self.assertEqual(invoice_total(250, 0), 0)

    def test_negative_quantity(self):
        with self.assertRaises(ValueError):
            invoice_total(250, -1)
