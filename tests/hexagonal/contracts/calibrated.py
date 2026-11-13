"""Building blocks for calibrated checks, which stay plain ``assert condition, "ID"`` statements.

A helper here answers a question and never asserts, so the one assertion site of a calibrated
identifier stays in the test or the contract function its inventory case declares.
"""


def refusal(call, code):
    """Whether ``call()`` is refused with ``code`` (or one of a tuple of codes).

    True when it raises an error whose ``code`` matches; False when it raises with another code or
    does not raise. An error without a ``code`` is not a refusal and propagates unchanged.
    """
    accepted = code if isinstance(code, tuple) else (code,)
    try:
        call()
    except Exception as error:
        if not hasattr(error, "code"):
            raise
        return error.code in accepted
    return False
