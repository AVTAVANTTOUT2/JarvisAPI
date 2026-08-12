"""Credentials Basic Auth à représentation systématiquement neutralisée."""

from __future__ import annotations

from dataclasses import dataclass
import re

import httpx


_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


@dataclass(frozen=True, slots=True, repr=False)
class BasicAuthCredentials:
    username: str
    password: str

    def __post_init__(self) -> None:
        if not _USERNAME.fullmatch(self.username):
            raise ValueError("Username Basic Auth OpenCode invalide")
        if len(self.password) < 24 or any(ord(char) < 32 for char in self.password):
            raise ValueError("Mot de passe Basic Auth OpenCode insuffisant ou invalide")

    def as_httpx(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self.username, self.password)

    def __repr__(self) -> str:
        return (
            f"BasicAuthCredentials(username={self.username!r}, password='[REDACTED]')"
        )
