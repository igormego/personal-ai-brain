"""Source-only description of capabilities already present in the trusted registry.

No capability is registered, granted, enabled or executed by this helper.
A later authenticated planning route may consume this bounded declaration.
"""
from __future__ import annotations

from .control import Registry


def discover_registered(registry: Registry) -> dict:
    """Return only configured metadata; never handler, credential or provider."""
    if not isinstance(registry, Registry):
        raise TypeError("invalid_registry")
    return {
        "schema_version": "0.1",
        "capabilities": [
            {
                "id": item.id,
                "description": item.description,
                "risk_level": item.risk_level,
                "side_effect_class": item.side_effect_class,
                "argument_schema": item.argument_schema,
            }
            for item in sorted(registry.capabilities.values(), key=lambda cap: cap.id)
        ],
    }
