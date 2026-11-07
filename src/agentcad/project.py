"""Project configuration (agentcad.toml) and isolated import of the model module."""
from __future__ import annotations

import importlib.util
import sys
import tomllib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from .model import Part
from .printers import Printer, get_printer


class ConfigError(ValueError):
    """The project configuration or its module path is unusable (CLI exit 2)."""


class BuildError(RuntimeError):
    """The model code raised or produced no valid solid (CLI exit 3)."""


DEFAULT_CHECKS: dict = {"max_overhang_deg": 45, "overhang_allow_mm2": 25, "min_wall_mm": 0,
                        "interference_tol_mm3": 0.01, "first_layer_mm": 0.3, "allow_overlap": []}
DEFAULT_EXPORT: dict = {"tolerance": 0.02, "angular_tolerance": 0.05}
ALLOWED_KEYS: dict[str, set[str]] = {"project": {"name", "module"}, "printer": {"profile", "bed"},
                                     "export": set(DEFAULT_EXPORT), "checks": set(DEFAULT_CHECKS)}


@dataclass
class Project:
    """A loaded project: validated configuration plus the parts its model module defines."""

    path: Path
    name: str
    parts: dict[str, Part]
    module: ModuleType
    printer: Printer
    export: dict
    checks: dict
    config_path: Path

    @classmethod
    def load(cls, path: str | Path = ".") -> Project:
        """Read agentcad.toml (a file or its folder), import the module and call ``parts()``."""
        source = Path(path).resolve()
        config_path = source / "agentcad.toml" if source.is_dir() else source
        data = _read_config(config_path)
        root = config_path.parent
        module_file = root / data["project"]["module"]
        if not module_file.is_file():
            raise ConfigError(f"Project module not found: {module_file}")
        export = {**DEFAULT_EXPORT, **data.get("export", {})}
        checks = {**DEFAULT_CHECKS, **data.get("checks", {})}
        _validate_numbers(export, checks)
        printer = _resolve_printer(data.get("printer", {}))
        module, parts = _import_model(module_file, root)
        for pair in checks["allow_overlap"]:
            missing = [n for n in pair if n not in {p.name for p in parts}]
            if missing:
                raise ConfigError(f"checks.allow_overlap names unknown part {missing[0]!r}")
        return cls(root, data["project"].get("name", root.name), {p.name: p for p in parts}, module, printer,
                   export, checks, config_path)

    @contextmanager
    def on_path(self) -> Iterator[None]:
        """Put the project folder first on ``sys.path`` so model code can import sibling files."""
        saved = sys.path[:]
        sys.path.insert(0, str(self.path))
        try:
            yield
        finally:
            sys.path[:] = saved


@dataclass
class BuildResult:
    """A published build: its folder, manifest and print-frame meshes by part name."""

    project: Project
    path: Path
    manifest: dict
    meshes: dict = field(default_factory=dict)


def _read_config(config_path: Path) -> dict:
    try:
        with config_path.open("rb") as stream:
            data = tomllib.load(stream)
    except OSError as exc:
        raise ConfigError(f"Cannot read {config_path}: {exc.strerror or exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
    for section, values in data.items():
        if section not in ALLOWED_KEYS:
            raise ConfigError(f"Unknown key '{section}' in {config_path.name}; allowed keys: "
                              f"{', '.join(ALLOWED_KEYS)}")
        if not isinstance(values, dict):
            raise ConfigError(f"'{section}' must be a TOML table")
        for key in values:
            if key not in ALLOWED_KEYS[section]:
                raise ConfigError(f"Unknown key '{section}.{key}' in {config_path.name}; allowed keys in "
                                  f"[{section}]: {', '.join(sorted(ALLOWED_KEYS[section]))}")
    module = data.get("project", {}).get("module")
    if not isinstance(module, str) or not module:
        raise ConfigError("project.module is required: the Python file that defines parts()")
    return data


def _validate_numbers(export: dict, checks: dict) -> None:
    for key, value in {**export, **{k: v for k, v in checks.items() if k != "allow_overlap"}}.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ConfigError(f"'{key}' must be a non-negative number, got {value!r}")
    if export["tolerance"] <= 0 or export["angular_tolerance"] <= 0:
        raise ConfigError("export.tolerance and export.angular_tolerance must be positive")
    if checks["max_overhang_deg"] > 90:
        raise ConfigError("checks.max_overhang_deg must be between 0 and 90")
    pairs = checks["allow_overlap"]
    if not isinstance(pairs, list) or any(
            not isinstance(p, list) or len(p) != 2 or not all(isinstance(n, str) for n in p) for p in pairs):
        raise ConfigError("checks.allow_overlap must be a list of [part, part] pairs")


def _resolve_printer(section: dict) -> Printer:
    try:
        printer = get_printer(section.get("profile", "generic-220"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if "bed" in section:
        bed = section["bed"]
        if not isinstance(bed, list) or len(bed) != 3 or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in bed):
            raise ConfigError("printer.bed must be three positive numbers [X, Y, Z] in mm")
        printer = Printer("custom", tuple(float(v) for v in bed), printer.nozzle_mm, "bed set in agentcad.toml")
    return printer


def _import_model(module_file: Path, root: Path) -> tuple[ModuleType, list[Part]]:
    # A unique module name per load keeps two projects (or two loads of one) from sharing state.
    name = f"_agentcad_model_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, module_file)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Cannot import project module {module_file}")
    module = importlib.util.module_from_spec(spec)
    saved_path = sys.path[:]
    sys.modules[name] = module
    try:
        sys.path.insert(0, str(root))
        spec.loader.exec_module(module)
        if not callable(getattr(module, "parts", None)):
            raise BuildError(f"{module_file.name} must define parts() returning a list of Part")
        parts = list(module.parts())
    except BuildError:
        raise
    except Exception as exc:
        raise BuildError(f"{module_file.name}: {type(exc).__name__}: {exc}") from exc
    finally:
        sys.path[:] = saved_path
        sys.modules.pop(name, None)
    if not parts:
        raise BuildError("parts() returned no parts")
    if not all(isinstance(p, Part) for p in parts):
        raise BuildError("parts() must return Part objects")
    duplicates = sorted({p.name for p in parts if [q.name for q in parts].count(p.name) > 1})
    if duplicates:
        raise BuildError(f"Part names must be unique; repeated: {', '.join(duplicates)}")
    return module, parts
