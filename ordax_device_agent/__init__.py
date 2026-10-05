"""Public compatibility package for the OrdaX Device Agent product name.

The implementation still lives in ordax_dev_agent during the compatibility
migration. Keeping the delegation explicit avoids breaking existing bootstrap,
Scheduled Task and recovery paths while new clients adopt the product name.
"""
from ordax_dev_agent import __version__

__all__ = ["__version__"]
