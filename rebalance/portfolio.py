import copy

import numpy as np
from loguru import logger
from rich.console import Console
from rich.table import Table

from . import rebalancing_helper
from .asset import Asset
from .money import Cash

_console = Console()


class TargetException(Exception):
    """
    Exception raised when target is not valid.
    """

    def __init__(self, message, target, total):
        self.message = message
        self.target = target
        self.total = total


class Portfolio:
    """
    Portfolio class.

    Defines a :class:`.Portfolio` of :class:`.Asset` s and :class:`.Cash` and performs rebalancing of the portfolio.

    """

    def __init__(self):
        """
        Initialization.
        """
        self._assets = {}
        self._cash = {}
        self._is_selling_allowed = False
        self._common_currency = "EUR"

    @property
    def common_currency(self):
        """
        str: Currency used as the pivot for allocation and optimisation calculations.
        """
        return self._common_currency

    @common_currency.setter
    def common_currency(self, currency):
        self._common_currency = currency.upper()

    @property
    def cash(self):
        """
        Dict[str, Cash]: Portfolio's dictionary of cash. The keys are currency symbols.
        """

        return self._cash

    @cash.setter
    def cash(self, cash):
        self._cash = cash

    def add_cash(self, amount, currency):
        """
        Adds cash to portfolio.

        Args:
            amount (float) : Amount of cash
            currency (str) : Currency of cash
        """

        if currency.upper() not in self._cash:
            self._cash[currency.upper()] = Cash(amount, currency)
        else:
            self._cash[currency.upper()].amount += amount

    def easy_add_cash(self, amounts, currencies):
        """
        An easy way of adding cash of various currencies to portfolio.

        Args:
            amounts (Sequence[float]): Amounts of cash from different curriencies.
            currencies (Sequence[str]): Specifies curriency of each of the amounts. Must be  in the same order as ``amounts``.

        """
        assert len(amounts) == len(currencies), (
            "`amounts` and `currencies` should be of the same length."
        )
        for amount, currency in zip(amounts, currencies, strict=True):
            self._cash[currency.upper()] = Cash(amount, currency)

    @property
    def assets(self):
        """
        Dict[str, Asset]: Dictionary of assets in portfolio. The keys of the dictionary are the tickers of the assets.


        No setter allowed.
        """
        return self._assets

    @property
    def selling_allowed(self):
        """
        bool: Flag indicating if selling of assets is allowed or not when rebalancing portfolio.
        """
        return self._is_selling_allowed

    @selling_allowed.setter
    def selling_allowed(self, flag):
        self._is_selling_allowed = flag

    def add_asset(self, asset):
        """
        Adds specified :class:`.Asset` to the portfolio.

        Args:
            asset (Asset): Asset to add to portfolio.
        """
        self._assets[asset.ticker] = copy.deepcopy(asset)

    def easy_add_assets(self, tickers, quantities):
        """
        An easy way to add multiple assets to portfolio.

        Args:
            tickers (Sequence[str]): Ticker of assets in portfolio.
            quantities (Sequence[float]): Quantities of respective assets in portfolio. Must be in the same order as ``tickers``.
        """

        assert len(tickers) == len(quantities), (
            "`names` and `quantities` must be of the same length."
        )

        for ticker, quantity in zip(tickers, quantities, strict=True):
            self._assets[ticker] = Asset(ticker, quantity)

    def asset_allocation(self):
        """
        Computes the portfolio's asset allocation.


        Returns:
            Dict[str, Asset]: Asset allocation of the portfolio (in %). The keys of the dictionary are the tickers of the assets.
        """

        # Obtain all market values in 1 currency (doesn't matter which)
        total_value = self.market_value(self._common_currency)

        total_value = max(
            1.0, total_value
        )  # protect against division by 0 (total_value = 0, means new portfolio)

        asset_allocation = {}
        for name, asset in self._assets.items():
            asset_allocation[name] = (
                asset.market_value_in(self._common_currency) / total_value * 100.0
            )

        return asset_allocation

    def market_value(self, currency):
        """
        Computes the total market value of the assets in the portfolio.

        Args:
            currency (str): The currency in which to obtain the value.

        Returns:
            float: The total market value of the assets in the portfolio.
        """

        total = 0.0
        for asset in self.assets.values():
            total += asset.market_value_in(currency)

        return total

    def cash_value(self, currency):
        """
        Computes the cash value in the portfolio.

        Args:
            currency (str): The currency in which to obtain the value.

        Returns:
            float: The total cash value in the portfolio.
        """

        total = 0.0
        for cash in self.cash.values():
            total += cash.amount_in(currency)

        return total

    def value(self, currency):
        """
        Computes the total value (cash and assets) in the portfolio.

        Args:
            currency (str): The currency in which to obtain the value.

        Returns:
            float: The total value in the portfolio.
        """

        return self.market_value(currency) + self.cash_value(currency)

    def buy_asset(self, ticker, quantity):
        """
        Buys (or sells) the specified amount of an asset.

        Args:
            ticker (str): Ticker of asset to buy.
            quantity (int): If positive, it is the quantity to buy. If negative, it is the quantity to sell.

        Return:
            float: Cost of transaction (in asset's own currency)
        """

        if quantity == 0:
            return 0.00

        asset = self.assets[ticker]
        cost = asset.buy(quantity)
        self.add_cash(-cost, asset.currency)
        return cost

    def exchange_currency(
        self, to_currency, from_currency, to_amount=None, from_amount=None
    ):
        """
        Performs currency exchange in Portfolio.

        Args:
            to_currency (str): Currency to which to perform the exchange
            from_currency (str): Currency from which to perform the exchange
            to_amount (float, optional): If specified, it is the amount to which we want to convert
            from_amount (float, optional): If specified, it is the amount from which we want to convert

        Note: either the `to_amount` or `from_amount` needs to be specifed.
        """

        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        # add cash instances of both currencies to portfolio if non-existent
        self.add_cash(0.0, from_currency)
        self.add_cash(0.0, to_currency)

        if to_amount is None and from_amount is None:
            raise Exception("Argument `to_amount` or `from_amount` must be specified.")

        if to_amount is not None and from_amount is not None:
            raise Exception(
                "Please specify only `to_amount` or `from_amount`, not both."
            )

        if to_amount is not None:
            from_amount = (
                self.cash[to_currency].exchange_rate(from_currency) * to_amount
            )
        elif from_amount is not None:
            to_amount = (
                self.cash[from_currency].exchange_rate(to_currency) * from_amount
            )

        self.add_cash(to_amount, to_currency)
        assert from_amount is not None
        self.add_cash(-from_amount, from_currency)

    def rebalance(self, target_allocation, verbose=False):
        """
        Rebalances the portfolio using the specified target allocation, the portfolio's current allocation,
        and the available cash.

        Args:
            target_allocation (Dict[str, float]): Target asset allocation of the portfolio (in %). The keys of the dictionary are the tickers of the assets.
            verbose (bool, optional): Verbosity flag. Default is False.

        Returns:
            (tuple): tuple containing:
                * new_units (Dict[str, int]): Units of each asset to buy. The keys of the dictionary are the tickers of the assets.
                * prices (Dict[str, [float, str]]): The keys of the dictionary are the tickers of the assets. Each value of the dictionary is a 2-entry list. The first entry is the price of the asset during the rebalancing computation. The second entry is the currency of the asset.
                * exchange_rates (Dict[str, float]): The keys of the dictionary are currencies. Each value is the exchange rate to USD during the rebalancing computation.
                * max_diff (float): Largest difference between target allocation and optimized asset allocation.
        """

        # order target_allocation dict in the same order as assets dict and upper key
        logger.info("Rebalancing portfolio ({} assets)", len(self._assets))
        target_allocation_reordered = {}
        try:
            for key in self.assets:
                target_allocation_reordered[key] = target_allocation[key]
        except KeyError as err:
            raise Exception(
                "'target_allocation not compatible with the assets of the portfolio."
            ) from err

        target_allocation_np = np.fromiter(
            target_allocation_reordered.values(), dtype=float
        )

        target_total = np.sum(target_allocation_np)

        if float(target_total) != 100.0:
            raise TargetException(
                "Target allocation must sum up to 100%.",
                target_allocation_np,
                target_total,
            )

        # offload heavy work
        (balanced_portfolio, new_units, prices, cost, exchange_history) = (
            rebalancing_helper.rebalance(self, target_allocation_reordered)
        )

        # compute old and new asset allocation
        # and largest diff between new and target asset allocation
        old_allocation = self.asset_allocation()
        new_allocation = balanced_portfolio.asset_allocation()
        largest_discrepancy = max(
            abs(
                target_allocation_np - np.fromiter(new_allocation.values(), dtype=float)
            )
        )

        if verbose:
            show_names = any(
                a.name is not None for a in balanced_portfolio.assets.values()
            )

            table = Table(show_header=True, header_style="bold")
            if show_names:
                table.add_column(
                    "Name", max_width=35, no_wrap=True, overflow="ellipsis"
                )
            else:
                table.add_column("Ticker")
            table.add_column("Price", justify="right")
            table.add_column("Δ Units", justify="right")
            table.add_column("Amount", justify="right")
            table.add_column("CCY")
            table.add_column("Old %", justify="right")
            table.add_column("New %", justify="right")
            table.add_column("Target %", justify="right")

            for ticker in balanced_portfolio.assets:
                is_winding_down = target_allocation[ticker] == 0.0
                row_style = "dim" if is_winding_down else ""

                qty = new_units[ticker]
                amt = cost[ticker]
                qty_fmt = f"{qty:,d}" if isinstance(qty, int) else f"{qty:,.3f}"
                if qty > 0:
                    qty_str = f"[green]{qty_fmt}[/green]"
                    amt_str = f"[green]{amt:,.0f}[/green]"
                elif qty < 0:
                    qty_str = f"[red]{qty_fmt}[/red]"
                    amt_str = f"[red]{amt:,.0f}[/red]"
                else:
                    qty_str = f"[dim]{qty_fmt}[/dim]"
                    amt_str = f"[dim]{amt:,.0f}[/dim]"

                new_a = new_allocation[ticker]
                tgt_a = target_allocation[ticker]
                new_alloc_str = (
                    f"[yellow]{new_a:.2f}[/yellow]"
                    if abs(new_a - tgt_a) > 0.5
                    else f"{new_a:.2f}"
                )

                asset_label = (
                    balanced_portfolio.assets[ticker].name or ticker
                    if show_names
                    else ticker
                )
                row = [asset_label]
                row += [
                    f"{prices[ticker][0]:,.2f}",
                    qty_str,
                    amt_str,
                    prices[ticker][1],
                    f"{old_allocation[ticker]:.2f}",
                    new_alloc_str,
                    f"{tgt_a:.2f}",
                ]
                table.add_row(*row, style=row_style)

            _console.print()
            _console.print(table)
            _console.print(
                f"Largest discrepancy between new and target allocation: [bold]{largest_discrepancy:.2f}%[/bold]"
            )

            if exchange_history:
                noun = (
                    "conversions are" if len(exchange_history) > 1 else "conversion is"
                )
                _console.print(
                    f"\nBefore making the above purchases, the following currency {noun} required:"
                )
                for (
                    from_amount,
                    from_currency,
                    to_amount,
                    to_currency,
                    rate,
                ) in exchange_history:
                    _console.print(
                        f"  {from_amount:.0f} {from_currency} → {to_amount:.0f} {to_currency} "
                        f"at a rate of {rate:.4f}"
                    )

            _console.print("\nRemaining cash:")
            for cash in balanced_portfolio.cash.values():
                _console.print(f"  {cash.amount:,.0f} {cash.currency}")

        # Now that we're done, we can replace old portfolio with the new one
        self.__dict__.update(balanced_portfolio.__dict__)
        logger.info(
            "Rebalancing complete (largest discrepancy: {:.2f}%)", largest_discrepancy
        )

        return (new_units, prices, exchange_history, largest_discrepancy)

    def _sell_everything(self):
        """
        Sells all assets in the portfolio and converts them to cash.
        """

        for ticker, asset in self._assets.items():
            self.buy_asset(ticker, -asset.quantity)

    def _combine_cash(self, currency=None):
        """
        Converts cash in portfolio to one currency.

        Args:
            currency (str, optional) If specified, it is the currency to which convert all cash. If None, it is set to `_common_currency`.
        """

        if currency is None:
            currency = self._common_currency

        cash_vals = list(
            self.cash.values()
        )  # needed since cash dict might increase in size
        for cash in cash_vals:
            if cash.currency == currency:
                continue

            self.exchange_currency(
                to_currency=currency,
                from_currency=cash.currency,
                from_amount=cash.amount,
            )

    def _smart_exchange(self, currency_amount):
        """
        Performs currency exchange between Portfolio's different sources of cash based on amount required per currency.

        Args:
            currency_amount (Dict[str, float]): Amount needed per currency. The keys of the dictionary are the currency.

        Returns:
            List[tuple]: tuple containing:
                    *  from_amount (float): Amount exchanged from currency indicated by `from_currency`
                    *  from_currency (str): Currency from which to perform the exchange
                    *  to_amount (float): Amount exchanged to currency indicated by `to_currency`
                    *  to_currency (str): Currency to which to perform the exchange
                    *  rate (float): Currency exchange rate from `from_currency` to `to_currency`
        """

        # first, compute amount we have to convert to and amount we have for conversion

        to_fund = {}
        available = copy.deepcopy(self.cash)
        for currency in currency_amount:
            if currency not in self.cash:
                available[currency] = Cash(0.00, currency)

            shortfall = currency_amount[currency] - available[currency].amount

            if shortfall > 0:
                to_fund[currency] = Cash(shortfall, currency)
                del available[currency]  # no extra cash available for conversion
            else:
                # no conversion will be necessary
                available[currency].amount -= currency_amount[currency]

        # perform currency exchange
        exchange_history = []
        for to_cash in to_fund.values():
            single_source_sufficient = False
            # Try converting one shot if possible
            for from_cash in available.values():
                if from_cash.amount_in(to_cash.currency) >= to_cash.amount:
                    # perform conversion
                    self.exchange_currency(
                        to_currency=to_cash.currency,
                        from_currency=from_cash.currency,
                        to_amount=to_cash.amount,
                    )

                    # update amount we have to convert to or amount we have for conversion
                    amt = to_cash.amount_in(from_cash.currency)

                    rate = from_cash.exchange_rate(to_cash.currency)
                    exchange_history.append(
                        (
                            amt,
                            from_cash.currency,
                            to_cash.amount,
                            to_cash.currency,
                            rate,
                        )
                    )

                    from_cash.amount -= amt
                    to_cash.amount = 0.00

                    # move to next 'to_cash'
                    single_source_sufficient = True
                    break

            # If we reached here,
            # it means we couldn't perform one currency exchange to meet our 'to_cash'
            # So we'll just convert whatever we can
            if not single_source_sufficient:
                for from_cash in available.values():
                    if from_cash.amount_in(to_cash.currency) >= to_cash.amount:
                        # perform conversion
                        self.exchange_currency(
                            to_currency=to_cash.currency,
                            from_currency=from_cash.currency,
                            to_amount=to_cash.amount,
                        )

                        amt = to_cash.amount_in(from_cash.currency)
                        rate = from_cash.exchange_rate(to_cash.currency)
                        exchange_history.append(
                            (
                                amt,
                                from_cash.currency,
                                to_cash.amount,
                                to_cash.currency,
                                rate,
                            )
                        )

                        # update amount we have to convert to and amount we have for conversion
                        from_cash.amount -= amt
                        to_cash.amount = 0.00
                    else:
                        self.exchange_currency(
                            to_currency=to_cash.currency,
                            from_currency=from_cash.currency,
                            from_amount=from_cash.amount,
                        )
                        amt = from_cash.amount_in(to_cash.currency)

                        rate = from_cash.exchange_rate(to_cash.currency)
                        exchange_history.append(
                            (
                                from_cash.amount,
                                from_cash.currency,
                                amt,
                                to_cash.currency,
                                rate,
                            )
                        )

                        # update amount we have to convert to and amount we have for conversion
                        to_cash.amount -= amt
                        from_cash.amount = 0.00

        return exchange_history

    def __str__(self):
        """
        Return formatted string of entire portfolio.
        """
        result = [""]

        result.append("Assets")
        for asset in self.assets.values():
            result.append(str(asset))

        result.append("")
        result.append("Cash")
        for cash in self.cash.values():
            result.append(str(cash))

        result.append("")
        result.append("Value")
        result.append(
            f"{self._common_currency} 1 {self.value(self._common_currency):.2f}"
        )

        return "\n".join(result)
