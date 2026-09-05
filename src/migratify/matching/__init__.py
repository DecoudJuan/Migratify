"""The matching engine.

This package is deliberately **pure**: no network, no credentials, no provider
imports beyond the neutral models. That is what makes the scorer testable
offline against a golden set of hard cases, and what lets the same code serve
both migration directions.
"""
