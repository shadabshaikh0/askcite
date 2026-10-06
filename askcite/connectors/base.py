"""What every connector has: form fields, a connection test, and how it becomes a source."""

from __future__ import annotations

from dataclasses import dataclass, field

from askcite.config import Settings, SourcesConfig


@dataclass
class Field:
    name: str
    label: str
    kind: str = "text"  # text | password | textarea | select | checkbox | number | list
    required: bool = False
    secret: bool = False  # stored encrypted, never shown again
    help: str = ""
    placeholder: str = ""
    default: object = None
    options: list[tuple[str, str]] = field(default_factory=list)  # (value, label) for select
    show_if: tuple[str, str] | None = None  # (other field, value): only relevant when that field has that value


@dataclass
class TestResult:
    ok: bool
    message: str
    details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False  # saving is refused (e.g. a database user that can write)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "message": self.message, "details": self.details, "warnings": self.warnings,
                "blocked": self.blocked}


class Connector:
    kind: str = ""
    title: str = ""
    description: str = ""
    multiple: bool = False  # can several of this kind be connected (e.g. several repos)?
    fields: list[Field] = []

    def default_name(self, config: dict) -> str:
        return self.kind

    def test(self, config: dict, secrets: dict, settings: Settings) -> TestResult:
        raise NotImplementedError

    def apply(self, sources: SourcesConfig, name: str, config: dict, secrets: dict) -> None:
        """Add this connector to the effective sources."""
        raise NotImplementedError

    def source_keys(self, name: str) -> list[str]:
        """Keys used in sync_run for this connector's syncs."""
        return []

    # ---- turning form values into typed config + secrets ---------------------------------------
    def parse(self, values: dict[str, object], previous_secrets: dict | None = None) -> tuple[dict, dict]:
        """Split submitted values into (config, secrets). A blank secret keeps the previously saved one."""
        config, secrets = {}, {}
        for item in self.fields:
            raw = values.get(item.name)
            if item.kind == "checkbox":
                value: object = str(raw).lower() in ("1", "true", "on", "yes") if raw is not None else False
            elif item.kind == "number":
                value = int(raw) if str(raw or "").strip() else item.default
            elif item.kind == "list":
                text = raw if isinstance(raw, str) else ",".join(raw or [])
                value = [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]
            else:
                value = (str(raw).strip() if raw is not None else "") or None
                if value is None and not item.secret and item.default is not None:
                    value = item.default
            if item.secret:
                if value:
                    secrets[item.name] = value
                elif previous_secrets and previous_secrets.get(item.name):
                    secrets[item.name] = previous_secrets[item.name]
            else:
                config[item.name] = value
        return config, secrets

    def missing(self, config: dict, secrets: dict) -> list[str]:
        missing = []
        for item in self.fields:
            if not item.required:
                continue
            if item.show_if and str(config.get(item.show_if[0])) != item.show_if[1]:
                continue
            value = secrets.get(item.name) if item.secret else config.get(item.name)
            if value in (None, "", []):
                missing.append(item.label)
        return missing
