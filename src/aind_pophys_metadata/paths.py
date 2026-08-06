"""Path helpers for input discovery and for provenance in emitted metadata.

Kept import-light (stdlib only) so it is cheap to import and testable without
pulling in a capsule library's heavy dependencies.
"""

from pathlib import Path
from typing import Optional


def find_one(
    directory: Path,
    pattern: str,
    recursive: bool = True,
    required: bool = True,
) -> Optional[Path]:
    """Return the first sorted match for ``pattern``, or ``None``/raise.

    Every glob should go through here so no ``next(glob(...))`` is ever left
    without a default: a bare ``StopIteration`` carries no filename and no
    directory, which is the least debuggable way for a pipeline to fail the
    moment an upstream capsule renames an artifact - exactly what a schema
    migration does.

    Parameters
    ----------
    directory : pathlib.Path
        Directory to search.
    pattern : str
        Glob pattern to match.
    recursive : bool, optional
        Search subdirectories too. Note ``rglob`` does not follow symlinks on
        Python <= 3.12, so pass ``False`` and glob the exact directory when
        the target sits behind one.
    required : bool, optional
        Raise when nothing matches instead of returning ``None``.

    Returns
    -------
    pathlib.Path or None
        The lexicographically first match, or ``None`` when nothing matched
        and ``required`` is False. Matches are sorted before selection
        because ``glob``/``rglob`` yield in filesystem order, which differs
        between machines and between runs - an unstable pick is not
        reproducible provenance.

    Raises
    ------
    FileNotFoundError
        If nothing matched and ``required`` is True. The message names both
        the pattern and the directory.
    """
    directory = Path(directory)
    matches = (
        directory.rglob(pattern) if recursive else directory.glob(pattern)
    )
    match = next(iter(sorted(matches)), None)
    if match is None and required:
        raise FileNotFoundError(
            f"No file matching {pattern!r} found under {directory}"
        )
    return match


def relative_to_root(root: Path, path: Path) -> str:
    """Return ``path`` relative to ``root``, as a string.

    Both sides are resolved first. Inside the pipeline ``/results`` and
    ``/data`` are symlinks into the task work directory, so comparing a
    resolved root against an unresolved path raises ``ValueError`` and leaks
    an absolute container path (e.g. ``/results/VISp_0/dff``) into the
    metadata.

    Parameters
    ----------
    root : pathlib.Path
        Directory the result should be relative to (the results or data root).
    path : pathlib.Path
        Path to express relative to ``root``.

    Returns
    -------
    str
        ``path`` relative to ``root``, or the absolute ``path`` when it is
        genuinely outside ``root`` (so provenance is never dropped entirely).
    """
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)
