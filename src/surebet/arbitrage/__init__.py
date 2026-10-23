"""Pure standard arbitrage calculations and allocation verification."""

from .math import equal_return_seed, inverse_sum
from .verify import verify_allocation

__all__ = ["equal_return_seed", "inverse_sum", "verify_allocation"]
