"""
Functions that the tests store and call.

They live in an importable module, so dill stores them by name, and the
in-process executors import them.
"""


def fail_odd(x):
    """x, or ValueError(str(x)) for an odd x"""
    if x % 2:
        raise ValueError(str(x))
    return x
