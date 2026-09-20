"""Compatibility import; the maintained implementation lives in pkm_workflow."""
import sys

from pkm_workflow import fetcher as _implementation

sys.modules[__name__] = _implementation
