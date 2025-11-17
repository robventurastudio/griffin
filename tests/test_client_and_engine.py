import datetime as dt
import unittest

from alpaca_trader.alpaca_client import build_events_url, normalize_base_url
from alpaca_trader.engine import fmt_datetime


class BaseUrlNormalizationTest(unittest.TestCase):
    def test_drops_trailing_slash_and_version(self):
        raw = "https://paper-api.alpaca.markets/v2/"
        self.assertEqual(normalize_base_url(raw), "https://paper-api.alpaca.markets")

    def test_defaults_when_missing(self):
        self.assertEqual(normalize_base_url(None), "https://paper-api.alpaca.markets")

    def test_builds_events_url(self):
        url = build_events_url("https://paper-api.alpaca.markets/v2/", "trades")
        self.assertEqual(url, "https://paper-api.alpaca.markets/events/v1/events/trades")

    def test_rejects_unknown_event(self):
        with self.assertRaises(ValueError):
            build_events_url("https://paper-api.alpaca.markets", "unknown")


class DateFormattingTest(unittest.TestCase):
    def test_formats_naive_datetime_as_utc(self):
        value = dt.datetime(2024, 1, 1, 15, 30, 0)
        self.assertEqual(fmt_datetime(value), "2024-01-01 15:30:00 UTC")

    def test_formats_aware_datetime(self):
        value = dt.datetime(2024, 1, 1, 10, 30, 0, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
        self.assertEqual(fmt_datetime(value), "2024-01-01 15:30:00 UTC")

    def test_rejects_missing_value(self):
        with self.assertRaises(ValueError):
            fmt_datetime(None)


if __name__ == "__main__":
    unittest.main()
