"""Who may ask what, based on access.yaml and Slack user groups."""

from __future__ import annotations

import time
from collections.abc import Callable

from askcite.config import AccessConfig


class AccessRules:
    def __init__(self, config: AccessConfig, usergroup_members: Callable[[str], list[str]] | None = None,
                 cache_seconds: int = 600):
        self.config = config
        self._usergroup_members = usergroup_members
        self._cache: dict[str, tuple[float, set[str]]] = {}
        self._cache_seconds = cache_seconds

    def channel_allowed(self, channel: str, is_dm: bool) -> bool:
        if is_dm:
            return self.config.allow_dms
        return not self.config.allowed_channels or channel in self.config.allowed_channels

    def _members(self, usergroup: str) -> set[str]:
        cached = self._cache.get(usergroup)
        if cached and time.monotonic() - cached[0] < self._cache_seconds:
            return cached[1]
        members = set(self._usergroup_members(usergroup)) if self._usergroup_members else set()
        self._cache[usergroup] = (time.monotonic(), members)
        return members

    def groups_for(self, user: str) -> set[str]:
        groups = set()
        for name, group in self.config.groups.items():
            if group.all_users or user in group.slack_users or any(
                    user in self._members(ug) for ug in group.slack_usergroups):
                groups.add(name)
        return groups
