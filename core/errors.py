"""
Typed A-LEMS errors carrying a permanent catalog code (C-ERR v2).

AlemsError is additive in 39.5.2d: no existing raise site is converted.
New code raises AlemsError (or a subclass) with a code from
config/error_codes.yaml so that classification never depends on the
exception class name, which may change under refactoring.
"""
from typing import Any, Optional

# Fallback code for failures nothing else describes (design section 1 rule 4).
GENERIC_CODE = "ALEMS-GEN-0000"


class AlemsError(Exception):
    """
    Base class for A-LEMS failures with a catalog code.

    Subclasses may set a class level code; an instance code overrides it.
    details are structured, non secret metadata (design section 9 scope).
    """

    code = GENERIC_CODE

    def __init__(self, message: str, code: Optional[str] = None, **details: Any):
        """
        Args:
            message: human readable text; redacted before it is persisted.
            code: catalog code; None keeps the class level code.
            details: structured context, never credentials.
        """
        super().__init__(message)
        if code is not None:
            # instance code wins over the class default (classification tier 1)
            self.code = code
        self.details = details
