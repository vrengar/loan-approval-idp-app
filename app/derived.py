"""Derived-value computations for loan-application underwriting.

Demonstrates how to add explainable derived metrics on top of DI-extracted
values (e.g. monthly -> annualized income, debt-to-income, etc.). Each
computation returns its inputs + formula so it is auditable downstream.
"""
from __future__ import annotations

from typing import Any


def annualize_income(*, gross_pay: float, periods_per_year: int) -> dict[str, Any]:
    annual = gross_pay * periods_per_year
    return {
        "metric": "annualized_income",
        "value": round(annual, 2),
        "inputs": {"gross_pay": gross_pay, "periods_per_year": periods_per_year},
        "formula": "gross_pay * periods_per_year",
    }


def average_monthly_balance(*, balances: list[float]) -> dict[str, Any]:
    if not balances:
        return {"metric": "avg_monthly_balance", "value": 0.0, "inputs": {"balances": []}}
    avg = sum(balances) / len(balances)
    return {
        "metric": "avg_monthly_balance",
        "value": round(avg, 2),
        "inputs": {"balances": balances},
        "formula": "sum(balances)/len(balances)",
    }
