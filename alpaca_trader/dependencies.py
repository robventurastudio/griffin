"""Runtime dependency checks with clear install guidance."""

from importlib.util import find_spec
from typing import Dict

REQUIRED_PACKAGES: Dict[str, str] = {
    "alpaca_trade_api": "alpaca-trade-api",
    "pandas": "pandas",
}


def check_runtime_dependencies() -> None:
    """Raise a friendly error when required packages are missing."""

    missing = [pkg for pkg in REQUIRED_PACKAGES if find_spec(pkg) is None]
    if not missing:
        return

    install_hints = ", ".join(
        f"`pip install {REQUIRED_PACKAGES[pkg]}`" for pkg in missing
    )
    raise ImportError(
        "Missing required packages: "
        + ", ".join(sorted(missing))
        + ". Install dependencies with `pip install -r requirements.txt` "
        f"(or individually: {install_hints})."
    )
