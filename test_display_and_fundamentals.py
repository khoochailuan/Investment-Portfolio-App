# Student: Khoo Chai Luan | Admin number: G2603254C | Section: A
"""Arrow-marked gains and losses, plus the watchlist's dividend and fundamentals feed."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from market_watch import trailing_dividends, trailing_fundamentals
from portfolio import DOWN, UP, dollars, percent
from watch_views import compact_amount

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


class ArrowTests(unittest.TestCase):
    def test_signed_money_and_percent_use_arrows(self):
        self.assertEqual(dollars(Decimal("12.5"), True), f"{UP} US$12.50")
        self.assertEqual(dollars(Decimal("-12.5"), True), f"{DOWN} US$12.50")
        self.assertEqual(percent(Decimal("-3.456")), f"{DOWN} 3.46%")
        self.assertEqual(percent(Decimal("2")), f"{UP} 2.00%")
        self.assertEqual(percent(Decimal("0")), "0.00%")
        self.assertEqual(percent(None), "—")

    def test_unsigned_amounts_keep_a_minus_sign(self):
        self.assertEqual(dollars(Decimal("-12.5")), "−US$12.50")


class FundamentalsTests(unittest.TestCase):
    def test_latest_ttm_figures_and_currency(self):
        payload = {
            "timeseries": {
                "result": [
                    {
                        "meta": {"symbol": ["X"]},
                        "trailingEBITDA": [
                            {"asOfDate": "2026-06-30", "currencyCode": "USD",
                             "reportedValue": {"raw": 100}},
                            {"asOfDate": "2026-03-31", "currencyCode": "USD",
                             "reportedValue": {"raw": 90}},
                        ],
                    }
                ]
            }
        }
        with patch("market_watch.yahoo_json", return_value=payload):
            found = trailing_fundamentals("X", NOW)
        self.assertEqual(found["ebitda"], ("100", "2026-06-30"))
        self.assertNotIn("eps", found)
        self.assertEqual(found["currency"], "USD")

    def test_twelve_month_dividends_ignore_older_payments(self):
        recent = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
        old = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
        payload = {"chart": {"result": [{"events": {"dividends": {
            "a": {"amount": Decimal("0.25"), "date": recent},
            "b": {"amount": Decimal("0.25"), "date": old},
        }}}]}}
        with patch("market_watch.yahoo_json", return_value=payload):
            self.assertEqual(trailing_dividends("X", NOW), "0.25")

    def test_compact_amounts(self):
        self.assertEqual(compact_amount(Decimal("391000000000"), "USD"), "US$391.0B")
        self.assertEqual(compact_amount(Decimal("-5200000"), "SGD"), "−S$5.2M")
        self.assertEqual(compact_amount(None, "USD"), "—")


if __name__ == "__main__":
    unittest.main()


class NewsAndChartTests(unittest.TestCase):
    def test_relative_time_labels(self):
        from watch_views import ago

        now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(ago("2026-11-21T11:59:30+00:00", now), "just now")
        self.assertEqual(ago("2026-11-21T11:48:00+00:00", now), "12m ago")
        self.assertEqual(ago("2026-11-21T09:00:00+00:00", now), "3h ago")
        self.assertEqual(ago("2026-11-19T12:00:00+00:00", now), "2d ago")
        self.assertEqual(ago("not a date", now), "")

    def test_price_picture_is_rendered(self):
        from charts import price_png

        data = price_png((0, 5, 10), (10.0, 12.0, 11.0), 10.5, ((0, 10.0),), (), "#00513F",
                         ((0, "a"), (10, "b")), 600)
        self.assertTrue(data.startswith(b"\x89PNG"))
