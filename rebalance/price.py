from . import Cash

from currency_converter import CurrencyConverter


class Price:
    """
    An instance of :class:`Price` holds a price and a currency.
    """

    def __init__(self, price, currency="USD"):
        """
        Initialization.

        Args:
            price (float): Price.
            currency (str, optional): Currency of price. Defaults to "USD".
        """
        self._price = price
        self._currency = currency.upper()

    @property
    def price(self):
        """
        (float): Price (in own's currency).
        """
        return self._price

    @property
    def currency(self):
        """
        (str): Currency of price.
        """
        return self._currency

    def price_in(self, currency):
        """
        Converts price in specified currency.

        Args:
            currency (str): Currency in which to convert the price.

        Returns:
            (float): Price in specified currency.
        """
        currency_exchange = Cash.currency_rates.convert(1, self.currency, currency.upper())

        return currency_exchange * self._price
