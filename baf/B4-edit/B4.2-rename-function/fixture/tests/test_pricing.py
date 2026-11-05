import unittest
from pricing import calc_total
from app import invoice

class PricingTests(unittest.TestCase):
    def test_total(self):
        self.assertEqual(calc_total([(5, 2), (3, 4)]), 22)
    def test_empty(self):
        self.assertEqual(invoice([]), 0)
