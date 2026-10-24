"""
Functions that count their runs in a file.

dill copies a function's closure, and every executor or attempt loads a
fresh copy, so a run counter must live outside the function: each run
appends a line to a log file.
"""


def logged(log, fail_times=0):
    """x * 2, after fail_times runs that raise; each run appends to log"""
    path = str(log)

    def fn(x):
        with open(path, "a") as f:
            f.write("run\n")
        with open(path) as f:
            run = len(f.read().splitlines())
        if run <= fail_times:
            raise ValueError(f"attempt {run}")
        return x * 2

    return fn


def runs(log):
    """The number of runs logged so far"""
    return len(log.read_text().splitlines()) if log.exists() else 0
