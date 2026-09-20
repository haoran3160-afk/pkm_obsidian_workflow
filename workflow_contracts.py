"""Compatibility import; the maintained implementation lives in pkm_workflow."""
import sys

from pkm_workflow import workflow_contracts as _implementation

sys.modules[__name__] = _implementation
