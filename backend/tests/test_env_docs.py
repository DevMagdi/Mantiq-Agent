"""The environment contract must not drift away from the code.

`docs/ENVIRONMENT.md` promises to document *every* variable the application reads and
`deploy/.env.example` is what an operator copies. Both are checked against `Settings`, so adding a
new setting without documenting it (or documenting a variable nobody reads) fails the build instead
of surfacing at 3 a.m. in production.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "ENVIRONMENT.md"
ENV_EXAMPLE = ROOT / "deploy" / ".env.example"

#: Read by the compose file / collector sidecar, never by `Settings`.
COMPOSE_ONLY = {"POSTGRES_PASSWORD", "OTEL_EXPORTER_OTLP_ENDPOINT"}

ENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=", re.M)


def _documented_names() -> set[str]:
    return set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", DOC.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", [DOC, ENV_EXAMPLE])
def test_the_documentation_files_exist(path: Path) -> None:
    assert path.exists(), f"{path} is missing; it is referenced from README.md and docs/RUNBOOK.md"


def test_every_setting_is_documented() -> None:
    documented = _documented_names()
    missing = sorted(name.upper() for name in Settings.model_fields if name.upper() not in documented)
    assert not missing, f"undocumented settings in docs/ENVIRONMENT.md: {missing}"


def test_the_env_template_is_a_subset_of_the_settings() -> None:
    known = {name.upper() for name in Settings.model_fields} | COMPOSE_ONLY
    declared = set(ENV_LINE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))
    unknown = sorted(declared - known)
    assert not unknown, f"deploy/.env.example declares variables nothing reads: {unknown}"


def test_the_env_template_does_not_ship_a_real_secret() -> None:
    """A template that ships a working key is a leaked key. Only the demo password is public."""
    allowed_placeholders = {"Demo!Pilot2026", "change-me", "verify-me", "fake", "true", "false", ""}
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for name, value in re.findall(r"^([A-Z][A-Z0-9_]*)=([^#\n]*)", text, re.M):
        value = value.strip()
        if not value or value in allowed_placeholders:
            continue
        assert not re.match(r"^(AC[0-9a-f]{32}|sk-[A-Za-z0-9]{20,}|SG\.[A-Za-z0-9_-]{16,})$", value), (
            f"{name} looks like a real credential; templates must stay blank"
        )
