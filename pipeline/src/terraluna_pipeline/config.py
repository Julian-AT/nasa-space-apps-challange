"""Typed loader for ``config/run.yaml`` (PRD 8.13).

The YAML file is the single source of the run settings. This module holds no copy of any
threshold and no defaults. It reads the file, checks the terrain block that T3 uses (PRD 6.3, 7)
and returns it as a frozen :class:`TerrainConfig`.

Rules:

- A missing or null terrain setting raises :class:`MissingConfigKeyError`. Nothing is filled in.
- Every other entry, including the kickoff choices that are still ``null`` (PRD 15, for example
  ``radar.snr_rule`` or ``targets.moon.primary_window_id``), is returned by
  :meth:`RunConfig.value` exactly as written. ``null`` stays ``None``.
- Numbers must be YAML numbers. Booleans, strings and non-finite values are rejected rather than
  coerced. PyYAML follows YAML 1.1, so ``1e3`` and ``1.0e3`` load as strings (write ``1000`` or
  ``1.0e+3``), and ``none`` loads as the string ``"none"``, not as null.
- A key given twice in one mapping is an error. PyYAML would otherwise keep the last one.

Every error is a :class:`ConfigError` (a ``ValueError``) and carries the dotted ``key`` it is
about.
"""

from __future__ import annotations

import copy
import itertools
import math
import re
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

import yaml

Resampler = Literal["aeqd_sphere"]
"""Window resamplers. D29 fixes one: spherical AEQD with bilinear lookup (PRD 7)."""

DetrendPolicy = Literal["none", "plane"]
"""Terrain detrend policies: raw relief (``none``) and plane-removed texture (``plane``)."""

RESAMPLERS: Final[tuple[Resampler, ...]] = ("aeqd_sphere",)
DETREND_POLICIES: Final[tuple[DetrendPolicy, ...]] = ("none", "plane")

DEFAULT_RUN_CONFIG: Final[Path] = Path(__file__).resolve().parents[3] / "config" / "run.yaml"
"""``config/run.yaml`` at the repository root.

The path is found from this file, which holds for the uv workspace's editable install that
the offline pipeline runs from (PRD 8.3). Pass a path explicitly anywhere else; a path that names
no file raises :class:`MissingRunConfigError`.
"""

_TERRAIN_KEYS: Final = frozenset(
    {
        "analysis_grid_m",
        "lags_m",
        "min_lags_for_H",
        "min_lag_span_factor",
        "detrend",
        "min_valid_fraction",
        "resampler",
    }
)
_INDEX = re.compile(r"0|[1-9][0-9]*")
_DOCUMENT_KEY: Final = "(document)"


# --- Errors -----------------------------------------------------------------------------------


class ConfigError(ValueError):
    """Base class for run-config errors. ``key`` is the dotted path of the entry concerned."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(f"{key}: {message}")
        self.key = key


class MissingConfigKeyError(ConfigError):
    """A required entry is absent, or a terrain setting is null."""


class MissingRunConfigError(ConfigError):
    """The run-config file does not exist, for example the default path outside the workspace."""


class UnknownConfigKeyError(ConfigError):
    """The terrain block has an entry the loader does not know, such as a misspelt key."""


class DuplicateConfigKeyError(ConfigError):
    """A mapping in the YAML file gives the same key twice."""


class InvalidConfigValueError(ConfigError):
    """An entry has the wrong type or lies outside its allowed range."""


class NonPositiveValueError(InvalidConfigValueError):
    """A length in metres, such as the analysis grid, is zero or negative."""


class UnknownResamplerError(InvalidConfigValueError):
    """``terrain.resampler`` is not one of :data:`RESAMPLERS`."""


class UnknownDetrendError(InvalidConfigValueError):
    """``terrain.detrend`` names a policy that is not one of :data:`DETREND_POLICIES`."""


# --- Records ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TerrainConfig:
    """Terrain settings for T3, read from ``config/run.yaml``.

    Lengths are metres as floats. ``window_m`` and ``stride_m`` come from ``targets``; the rest
    come from ``terrain`` and keep their YAML names.
    """

    analysis_grid_m: float
    """Spacing of the metric analysis grid (``terrain.analysis_grid_m``)."""
    lags_m: tuple[float, ...]
    """Configured lags, strictly increasing (``terrain.lags_m``)."""
    min_lags_for_H: int
    """Fewest supported lags an H fit needs (``terrain.min_lags_for_H``)."""
    min_lag_span_factor: float
    """Smallest ratio of the largest to the smallest supported lag for an H fit."""
    detrend: tuple[DetrendPolicy, ...]
    """Detrend policies to compute, in file order (``terrain.detrend``)."""
    min_valid_fraction: float
    """Smallest valid-cell fraction a window may have, in (0, 1]."""
    resampler: Resampler
    """Window resampler (``terrain.resampler``)."""
    window_m: float
    """Target window width (``targets.window_m``)."""
    stride_m: float
    """Target window stride (``targets.stride_m``)."""


@dataclass(frozen=True, slots=True)
class RunConfig:
    """The parsed run config and its checked terrain block."""

    path: Path
    """The file the config was read from."""
    document: Mapping[str, object]
    """The YAML document exactly as parsed. Treat it as read-only; use :meth:`value`."""
    terrain: TerrainConfig

    def value(self, key: str) -> object:
        """Return the entry at a dotted ``key``, unchanged and as a copy.

        List items are addressed by index, as in ``targets.mars.0.primary_window_id``. A null
        entry returns ``None``; nothing is defaulted. An absent key raises
        :class:`MissingConfigKeyError`.
        """
        return copy.deepcopy(_lookup(self.document, key))


# --- Loading ----------------------------------------------------------------------------------


def load_run_config(path: Path = DEFAULT_RUN_CONFIG) -> RunConfig:
    """Read and check a run config. The file is only read, never written."""
    document = _read_document(path)
    return RunConfig(path=path, document=document, terrain=_terrain_config(document))


def load_terrain_config(path: Path = DEFAULT_RUN_CONFIG) -> TerrainConfig:
    """Read a run config and return its checked terrain settings."""
    return load_run_config(path).terrain


class _RunConfigLoader(yaml.SafeLoader):
    """PyYAML's safe loader, except that a key given twice in a mapping is an error."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, object]:
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, Hashable):
                continue  # The base class rejects unhashable keys with its own error.
            if key in seen:
                line = key_node.start_mark.line + 1
                raise DuplicateConfigKeyError(str(key), f"given twice in one mapping (line {line})")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _read_document(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise MissingRunConfigError(
            _DOCUMENT_KEY,
            f"{path} does not exist; pass the path of config/run.yaml explicitly when the "
            "pipeline does not run from the uv workspace",
        ) from error
    document = yaml.load(text, Loader=_RunConfigLoader)
    if not isinstance(document, dict):
        raise InvalidConfigValueError(
            _DOCUMENT_KEY, f"{path} must hold a YAML mapping, got {type(document).__name__}"
        )
    return document


def _lookup(document: Mapping[str, object], key: str) -> object:
    node: object = document
    for segment in key.split("."):
        if isinstance(node, Mapping) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and _INDEX.fullmatch(segment) and int(segment) < len(node):
            node = node[int(segment)]
        else:
            raise MissingConfigKeyError(key, "not present in the run config")
    return node


def _block(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    block = _required(document, key)
    if not isinstance(block, Mapping):
        raise InvalidConfigValueError(key, f"must be a mapping, got {type(block).__name__}")
    return block


def _required(document: Mapping[str, object], key: str) -> object:
    value = _lookup(document, key)
    if value is None:
        raise MissingConfigKeyError(key, "is null; T3 settings have no default")
    return value


def _terrain_config(document: Mapping[str, object]) -> TerrainConfig:
    terrain = _block(document, "terrain")
    _block(document, "targets")
    unknown = sorted(str(key) for key in set(terrain) - _TERRAIN_KEYS)
    if unknown:
        raise UnknownConfigKeyError(f"terrain.{unknown[0]}", "is not a known terrain setting")

    def setting(key: str) -> tuple[str, object]:
        dotted = f"terrain.{key}"
        return dotted, _required(document, dotted)

    return TerrainConfig(
        analysis_grid_m=_positive(*setting("analysis_grid_m")),
        lags_m=_lags(*setting("lags_m")),
        min_lags_for_H=_min_lags(*setting("min_lags_for_H")),
        min_lag_span_factor=_span_factor(*setting("min_lag_span_factor")),
        detrend=_detrend(*setting("detrend")),
        min_valid_fraction=_fraction(*setting("min_valid_fraction")),
        resampler=_resampler(*setting("resampler")),
        window_m=_positive("targets.window_m", _required(document, "targets.window_m")),
        stride_m=_positive("targets.stride_m", _required(document, "targets.stride_m")),
    )


# --- Value checks -----------------------------------------------------------------------------


def _real(key: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidConfigValueError(key, f"must be a number, got {value!r}")
    try:
        number = float(value)
    except OverflowError:
        raise InvalidConfigValueError(key, f"is too large for float64: {value!r}") from None
    if not math.isfinite(number):
        raise InvalidConfigValueError(key, f"must be finite, got {value!r}")
    return number


def _positive(key: str, value: object) -> float:
    number = _real(key, value)
    if number <= 0:
        raise NonPositiveValueError(key, f"must be positive, got {value!r}")
    return number


def _lags(key: str, value: object) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise InvalidConfigValueError(key, f"must be a non-empty list of lags, got {value!r}")
    lags = tuple(_positive(key, lag) for lag in value)
    if any(later <= earlier for earlier, later in itertools.pairwise(lags)):
        raise InvalidConfigValueError(key, f"must be strictly increasing, got {value!r}")
    return lags


def _min_lags(key: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidConfigValueError(key, f"must be an integer, got {value!r}")
    if value < 2:
        raise InvalidConfigValueError(key, f"must be at least 2 to fit a slope, got {value!r}")
    return value


def _span_factor(key: str, value: object) -> float:
    number = _real(key, value)
    if number < 1:
        raise InvalidConfigValueError(key, f"is a ratio of lags and must be at least 1: {value!r}")
    return number


def _fraction(key: str, value: object) -> float:
    number = _real(key, value)
    if not 0 < number <= 1:
        raise InvalidConfigValueError(key, f"must lie in (0, 1], got {value!r}")
    return number


def _detrend(key: str, value: object) -> tuple[DetrendPolicy, ...]:
    if not isinstance(value, list) or not value:
        raise InvalidConfigValueError(key, f"must be a non-empty list of policies, got {value!r}")
    for policy in value:
        if policy not in DETREND_POLICIES:
            known = ", ".join(DETREND_POLICIES)
            raise UnknownDetrendError(key, f"unknown policy {policy!r}; known: {known}")
    if len(set(value)) != len(value):
        raise InvalidConfigValueError(key, f"lists a policy twice: {value!r}")
    return tuple(cast(DetrendPolicy, policy) for policy in value)


def _resampler(key: str, value: object) -> Resampler:
    if value not in RESAMPLERS:
        known = ", ".join(RESAMPLERS)
        raise UnknownResamplerError(key, f"unknown resampler {value!r}; known: {known}")
    return value
