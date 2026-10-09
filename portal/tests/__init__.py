# The Django test client cannot render a template on this Python without this.
# See core/test_support.py.
from core.test_support import apply as _apply_context_shim

_apply_context_shim()
