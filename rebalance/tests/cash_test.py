import unittest

import requests_cache

from rebalance import Cash, Price


class TestCash(unittest.TestCase):
    def test_interface(self):
        """
        Test interface of Cash class.
        """
        amount = 20.4
        currency = "CAD"
        cash = Cash(amount=amount, currency=currency)
        self.assertEqual(cash.amount, amount)
        self.assertEqual(cash.currency, currency.upper())

        # currency conversion to itself
        self.assertEqual(cash.amount_in(currency), amount)

        # verify internal consistency: amount_in == exchange_rate * amount
        ex_rate = cash.exchange_rate("usd")
        self.assertGreater(ex_rate, 0)
        self.assertAlmostEqual(cash.amount_in("usd"), ex_rate * amount, 10)


class TestPrice(unittest.TestCase):
    def test_interface(self):
        """
        Test interface of Price class.
        """
        price = 20.4
        currency = "CAD"
        p = Price(price=price, currency=currency)
        self.assertEqual(p.price, price)
        self.assertEqual(p.currency, currency.upper())

        # currency conversion to itself
        self.assertEqual(p.price_in(currency), price)

        # verify internal consistency using the same FX source
        ex_rate = Cash(1, currency).exchange_rate("usd")
        self.assertAlmostEqual(p.price_in("usd"), ex_rate * price, 10)


if __name__ == "__main__":
    requests_cache.install_cache("asset_test")
    unittest.main()
