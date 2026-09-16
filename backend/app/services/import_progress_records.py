from __future__ import annotations

from collections.abc import Mapping, Sequence


def progress_projects_count(
    persisted_projects: int | None,
    parsed_payload: Mapping[str, object] | None,
    group_projects: Sequence[object] | None,
) -> int:
    if isinstance(parsed_payload, Mapping) and parsed_payload.get("document"):
        return len(group_projects or [])
    return int(persisted_projects or 0)
