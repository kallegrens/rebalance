from functools import lru_cache

import yfinance as yf


def fetch_fx_rate(from_currency: str, to_currency: str) -> float:
    """Fetch the current FX rate from *from_currency* to *to_currency* via yfinance.

    Args:
        from_currency (str): ISO 4217 currency code to convert from (e.g. ``"SEK"``).
        to_currency (str): ISO 4217 currency code to convert to (e.g. ``"EUR"``).

    Returns:
        float: Units of *to_currency* per one unit of *from_currency*.
    """
    if from_currency == to_currency:
        return 1.0
    ticker = f"{from_currency}{to_currency}=X"
    return float(yf.Ticker(ticker).fast_info["lastPrice"])


@lru_cache(maxsize=32)
def _cached_fx_rate(from_currency: str, to_currency: str) -> float:
    """Cached wrapper around :func:`.fetchers.fetch_fx_rate`.

    The cache lives for the duration of the process, so each currency pair is
    fetched at most once per run regardless of how many assets share the same
    currencies.
    """
    return fetch_fx_rate(from_currency, to_currency)


class Cash:
    """
    An instance of :class:`Cash` holds an amount and a currency.
    """

    def __init__(self, amount, currency="USD"):
        """
        Initialization.

        Args:
            amount (float): Amount of cash.
            currency (str, optional): Currency of cash. Defaults to "USD".
        """

        self._amount = amount
        self._currency = currency.upper()

    @property
    def amount(self):
        """
        (float): Amount of cash.
        """
        return self._amount

    @amount.setter
    def amount(self, amount):
        self._amount = amount

    @property
    def currency(self):
        """
        (str): Currency of cash.
        """
        return self._currency

    def amount_in(self, currency):
        """
        Converts amount of cash in specified currency.

        Args:
            currency (str): Currency in which to convert the amount of cash.

        Returns:
            (float): Amount of cash in specified currency.
        """

        return self.exchange_rate(currency) * self._amount

    def exchange_rate(self, currency):
        """
        Obtain the exchange rate from ``cash``'s own currency to specified currency.

        Args:
            currency (str): Currency.

        Returns:
            (float): exchange rate.
        """

        return _cached_fx_rate(self.currency, currency.upper())


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
        return _cached_fx_rate(self.currency, currency.upper()) * self._price
