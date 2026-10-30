"""OpenAlex API access with bounded, persistent request budgets."""

from .client import APIError, BudgetExceeded, OpenAlexClient
from .search import SearchSpec

__all__ = ["APIError", "BudgetExceeded", "OpenAlexClient", "SearchSpec"]
