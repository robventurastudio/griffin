import datetime as dt
import sys
import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pandas as pd

from alpaca_trader.alpaca_client import build_events_url, normalize_base_url
from alpaca_trader.engine import fmt_datetime, log_clock


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

    def test_parses_pandas_timestamp(self):
        value = pd.Timestamp("2024-01-01T15:30:00Z")
        self.assertEqual(fmt_datetime(value), "2024-01-01 15:30:00 UTC")

    def test_handles_iso_string(self):
        value = "2024-01-01T15:30:00+00:00"
        self.assertEqual(fmt_datetime(value), "2024-01-01 15:30:00 UTC")


class ClockLoggingTest(unittest.TestCase):
    def test_renders_clock_dict(self):
        clock = {
            "timestamp": dt.datetime(2024, 1, 1, 15, 30, tzinfo=dt.timezone.utc),
            "is_open": False,
            "next_open": "2024-01-02T14:30:00Z",
            "next_close": pd.Timestamp("2024-01-02T21:00:00Z"),
        }

        message = log_clock(clock)
        self.assertIn("open", message)
        self.assertIn("next_open=2024-01-02 14:30:00 UTC", message)
        self.assertIn("next_close=2024-01-02 21:00:00 UTC", message)


class RunOpenCloseScriptTest(unittest.TestCase):
    def test_script_injects_repo_root(self):
        root = Path(__file__).resolve().parents[1]
        script_path = root / "scripts" / "run_open_close.py"

        # Simulate running from the scripts directory where repo root is not on sys.path.
        import sys

        original_path = list(sys.path)
        sys.path = [p for p in sys.path if p != str(root)]
        try:
            spec = spec_from_file_location("run_open_close", script_path)
            module = module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)

            self.assertEqual(module.ROOT, root)
            self.assertIn(str(root), sys.path)
            self.assertTrue((module.ROOT / "alpaca_trader").is_dir())
        finally:
            sys.path = original_path

    def test_script_raises_if_repo_root_missing(self):
        root = Path(__file__).resolve().parents[1]
        script_path = root / "scripts" / "run_open_close.py"
        original_path = list(sys.path)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_script = Path(tmpdir) / "run_open_close.py"
                tmp_script.write_text(script_path.read_text())

                spec = spec_from_file_location("run_open_close_missing", tmp_script)
                module = module_from_spec(spec)
                assert spec.loader is not None

                with self.assertRaises(ImportError):
                    spec.loader.exec_module(module)
        finally:
            sys.path = original_path


if __name__ == "__main__":
    unittest.main()
