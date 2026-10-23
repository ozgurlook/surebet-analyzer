"""Connection-scoped WAMP feed snapshot and delta state."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import cast

_METADATA = frozenset(("changeType", "changedProperties"))
_CREATE = frozenset(("create", "created", "add", "added"))
_UPDATE = frozenset(("update", "updated", "change", "changed"))
_DELETE = frozenset(("delete", "deleted", "remove", "removed"))


class WampFeedState:
    """Reduce full records and deltas without exposing mutable internal state."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, object]] = {}
        self._rejected = 0

    @property
    def rejected_count(self) -> int:
        return self._rejected

    def apply_full(self, records: Iterable[Mapping[str, object]]) -> None:
        replacement: dict[tuple[str, str], dict[str, object]] = {}
        for candidate in records:
            record = self._record(candidate)
            if record is None:
                continue
            key = self._key(record)
            if key is None:
                continue
            replacement[key] = record
        self._records = replacement

    def apply_delta(self, records: Iterable[Mapping[str, object]]) -> None:
        updated = deepcopy(self._records)
        for candidate in records:
            if not isinstance(candidate, Mapping):
                self._rejected += 1
                continue
            key = self._key(candidate)
            if key is None:
                continue
            change = candidate.get("changeType")
            if not isinstance(change, str):
                self._rejected += 1
                continue
            change = change.strip().lower()
            if change in _DELETE:
                updated.pop(key, None)
                continue
            if change in _CREATE:
                properties = candidate.get("changedProperties", {})
                if not isinstance(properties, Mapping) or self._changes_identity(properties):
                    self._rejected += 1
                    continue
                created = {k: deepcopy(v) for k, v in candidate.items() if k not in _METADATA}
                created.update(deepcopy(dict(properties)))
                updated[key] = created
                continue
            if change in _UPDATE:
                properties = candidate.get("changedProperties")
                if (
                    not isinstance(properties, Mapping)
                    or self._changes_identity(properties)
                    or key not in updated
                ):
                    self._rejected += 1
                    continue
                updated[key].update(deepcopy(dict(properties)))
                continue
            self._rejected += 1
        self._records = updated

    def snapshot(self) -> tuple[Mapping[str, object], ...]:
        return cast(
            tuple[Mapping[str, object], ...],
            tuple(deepcopy(record) for record in self._records.values()),
        )

    def _record(self, candidate: object) -> dict[str, object] | None:
        if not isinstance(candidate, Mapping):
            self._rejected += 1
            return None
        key = self._key(candidate)
        if key is None:
            return None
        return deepcopy(dict(candidate))

    def _key(self, record: Mapping[str, object]) -> tuple[str, str] | None:
        entity_type = record.get("entityType")
        if not isinstance(entity_type, str) or not entity_type.strip():
            entity_type = record.get("_type")
        identifier = record.get("id")
        if not isinstance(entity_type, str) or not entity_type.strip():
            self._rejected += 1
            return None
        if identifier is None or isinstance(identifier, bool):
            self._rejected += 1
            return None
        normalized_id = str(identifier).strip()
        if not normalized_id:
            self._rejected += 1
            return None
        return entity_type.strip(), normalized_id

    @staticmethod
    def _changes_identity(properties: Mapping[object, object]) -> bool:
        return "entityType" in properties or "_type" in properties or "id" in properties
