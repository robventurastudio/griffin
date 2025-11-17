"""Global configuration defaults for the retail trader package."""

#: How many shares to trade per symbol when the simple open/close strategy
#: enters a position. This value can be overridden per-invocation, but keeping
#: it in one place makes it easy to tune sizing for experiments.
TRADER_QTY = 1

#: How often (in seconds) the engine polls Alpaca for the latest clock
#: information while it waits for either the market open or close. The default
#: is intentionally conservative so that the script can be left running on a
#: small VPS without generating excessive API traffic.
POLL_INTERVAL_SECONDS = 30
