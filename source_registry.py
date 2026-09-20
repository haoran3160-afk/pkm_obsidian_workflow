"""Compatibility import; the maintained implementation lives in pkm_workflow."""
import sys

from pkm_workflow import source_registry as _implementation

sys.modules[__name__] = _implementation
