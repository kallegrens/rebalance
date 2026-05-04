from currency_converter import CurrencyConverter


class Cash:
    """
    An instance of :class:`Cash` holds an amount and a currency.

    Attributes
        currency_rates (currency_converter.CurrencyConverter) : Used for currency conversion.

    """

    currency_rates = CurrencyConverter()

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

        return Cash.currency_rates.convert(1, self.currency, currency.upper())


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
        currency_exchange = Cash.currency_rates.convert(
            1, self.currency, currency.upper()
        )

        return currency_exchange * self._price
