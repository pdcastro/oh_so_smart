"""Supporting error handling functions.

Copyright (C) 2025 Paulo Ferreira de Castro

Licensed under the Open Software License version 3.0, a copy of which can be
found in the LICENSE file.
"""

from typing import cast


def fmt_exception_group(
    exc_group: ExceptionGroup | BaseExceptionGroup,
    header="",
    indent="    ",
) -> str:
    """Produce an ExceptionGroup summary string with Exception per line,
    recursing over inner ExceptionGroup exceptions.

    Sample output given header="Exiting with exceptions:" and one inner
    ExceptionGroup exception:

    Exiting with exceptions:
    ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
        └> GPIOError: TEST OF GPIOError bubbling

    Sample output given header="Exiting with exceptions:", no inner
    ExceptionGroup exceptions, and a single exception in the root group:

    Exiting with exceptions: SignalException: SIGINT (2)
    """

    def fmt_notes(*args: BaseException):
        notes: list[str] = [
            note for exc in args for note in getattr(exc, "__notes__", [])
        ]
        return "(" + ") (".join(notes) + ")" if notes else ""

    def fmt(group: ExceptionGroup | BaseExceptionGroup, level: int, result: list[str]):
        for exc in cast(tuple[BaseException], group.exceptions):
            prefix = indent * level
            if level:
                prefix += "└> "
            notes = fmt_notes(group, exc)
            msg = f"{prefix}{type(exc).__name__}: {exc} {notes}"
            result.append(msg)
            if isinstance(exc, (ExceptionGroup, BaseExceptionGroup)):
                msg += ":"
                fmt(exc, level + 1, result)
        return result

    result = fmt(exc_group, 0, [])
    if header and result:
        if len(result) > 1:
            result.insert(0, header)
        else:
            result[0] = header + " " + result[0]

    return "\n".join(result)
