"""Mask IBKR account IDs in log output.

ib_async logs whole upstream objects (``Position``, ``PortfolioItem``,
``Trade``, ``Fill``) at INFO and WARNING, and their reprs carry the account
(``account=``, ``acctNumber=``, ``clearingAccount=``). Raising those loggers'
levels would still leak via the WARNING reprs and would also hide useful
gateway connectivity messages, so the redaction happens at the formatter —
the one place every record passes through on its way out.
"""

import logging
import re

ACCOUNT_MASK = "***"

# Any quoted repr field whose name mentions an account: account='U1234567',
# acctNumber='U1234567', clearingAccount='…'. Empty values stay as-is.
_ACCOUNT_FIELD_RE = re.compile(
    r"""\b(\w*(?:account|acct)\w*)=(['"])([^'"]+)\2""", re.IGNORECASE,
)

# Bare IDs in free text (e.g. IB error strings): U/F individual and advisor
# accounts, with a D prefix for their paper counterparts.
_ACCOUNT_ID_RE = re.compile(r"\bD?[UF]\d{5,}\b")


def redact_account_ids(text: str) -> str:
    """Return *text* with every IBKR account ID replaced by ``ACCOUNT_MASK``."""
    text = _ACCOUNT_FIELD_RE.sub(rf"\1=\2{ACCOUNT_MASK}\2", text)
    return _ACCOUNT_ID_RE.sub(ACCOUNT_MASK, text)


class AccountRedactingFormatter(logging.Formatter):
    """Formatter that masks account IDs in the fully rendered record.

    Redacting the final string (rather than ``record.msg``) also covers
    ``%``-style args and exception tracebacks.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_account_ids(super().format(record))
