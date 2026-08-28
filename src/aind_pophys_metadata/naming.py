"""Plane and FOV id construction."""

from typing import List, Optional, Tuple


def fov_id(
    plane_index: int,
    acronym: Optional[str],
    *,
    single_plane: bool,
) -> str:
    """Return the canonical FOV/plane id under the active naming scheme.

    The id names output folders, so it must be identical across every capsule
    for the same input.

    - single-plane -> ``"plane_{plane_index}"``.
    - multi-plane -> ``"{acronym}_{plane_index}"``, falling back to
      ``"plane_{plane_index}"`` so a missing acronym never yields
      ``"None_0"``.

    Parameters
    ----------
    plane_index : int
        Index of the plane within the imaging session.
    acronym : str or None
        CCF acronym of the plane's targeted structure, or ``None``.
    single_plane : bool
        Whether the acquisition has exactly one plane.

    Returns
    -------
    str
        The FOV/plane identifier.
    """
    if single_plane or not acronym:
        return f"plane_{plane_index}"
    return f"{acronym}_{plane_index}"


def build_fov_ids(pairs: List[Tuple[int, Optional[str]]]) -> Tuple[str, ...]:
    """Resolve canonical FOV ids for ``(plane_index, acronym)`` pairs.

    Sorts by plane index and applies :func:`fov_id` once per plane; single-
    vs multi-plane is decided here (exactly one plane -> single).

    Parameters
    ----------
    pairs : list of (int, str or None)
        Per-plane ``(plane_index, targeted_structure_acronym)`` pairs.

    Returns
    -------
    tuple of str
        Canonical FOV ids ordered by plane index; empty when ``pairs`` is
        empty.
    """
    pairs = sorted(pairs, key=lambda p: p[0])
    single_plane = len(pairs) == 1
    return tuple(
        fov_id(idx, acronym, single_plane=single_plane)
        for idx, acronym in pairs
    )
