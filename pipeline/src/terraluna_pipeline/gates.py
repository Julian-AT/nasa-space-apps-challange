"""Release gates: the only writer of outputs/validation.json (PRD 9; CONTRACTS c.3 and d).

Producers never write outputs/validation.json. Each one writes its own fragment
``outputs/gates/<GATE>.json`` (one GateRecord) and its evidence file through
:func:`write_fragment`, then runs ``gates merge`` and commits both files. When two branches both
changed outputs/validation.json, nobody merges it by hand: keep both fragments and rerun
``gates merge``.

Commands (``python -m terraluna_pipeline.gates <subcommand>``, CONTRACTS f):

- ``merge`` writes outputs/validation.json: exactly seven GateRecords in G0 to G6 order, each
  the canonical JSON of its source. Sources: G0 from ``outputs/gates/G0.json`` (an attestation)
  if present, else the GateRecord block ``gate_record`` in ``outputs/pilot/g0_evidence.json``
  (NOT_RUN or PARTIAL only); G1 from ``fixtures/polarimetry/g1-record.json``; G2 to G6 from
  ``outputs/gates/G2.json`` to ``G6.json``. A gate without a source is NOT_RUN with every
  nullable field null and ``input_hashes: []``. Merge never promotes a status and never reads a
  clock.
- ``verify`` is offline and writes nothing: it re-hashes every entry's ``input_hashes``, checks
  ``config_version`` against the current config/run.yaml (``stale_run_id``), re-checks the G0
  attestation, and requires the committed outputs/validation.json to equal the merge output
  byte for byte.
- ``attest-g0 --mode A|B|C --operator NAME --confirm I-RECORD-THE-G0-DECISION`` records the
  G0 decision of a person, or of the orchestrator under the team's recorded delegation
  (docs/ralph/DECISIONS.md). No Make target, check or CI step runs it. It writes
  ``outputs/gates/G0_attestation.json`` (mode, operator, the sha256 of each G0 evidence file
  and of config/run.yaml; an engineering choice) and ``outputs/gates/G0.json`` (PASS). The
  attested mode must equal config ``run.shipping_mode``, which a person sets first; this module
  never writes config.
- ``snapshot --bundle-version V --out PATH`` copies G0 to G5 from outputs/validation.json and
  writes G6 as NOT_RUN for bundle V (CONTRACTS c.3). PRD-02 (may the shipped /methods show G6
  NOT_RUN?) is decided: yes (DECISIONS.md).

Exit codes: 0 done, 1 a check failed, 2 usage, 3 blocked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

import yaml
from pydantic import ValidationError

from terraluna_pipeline import provenance
from terraluna_pipeline.schema import GateRecord

MODULE: Final = "terraluna_pipeline.gates"
REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

GATE_IDS: Final = ("G0", "G1", "G2", "G3", "G4", "G5", "G6")
VALIDATION_PATH: Final = "outputs/validation.json"
GATES_DIR: Final = "outputs/gates"
CONFIG_PATH: Final = "config/run.yaml"
PILOT_G0_EVIDENCE: Final = "outputs/pilot/g0_evidence.json"
G1_RECORD: Final = "fixtures/polarimetry/g1-record.json"
G0_ATTESTATION: Final = "outputs/gates/G0_attestation.json"
MOCK_BUNDLE_DIR: Final = "fixtures/mock-bundle"
CONFIRM_G0: Final = "I-RECORD-THE-G0-DECISION"

G0_EVIDENCE_FILES: Final = (
    "outputs/g0/earth-radar-roi.json",
    "outputs/g0/web-evidence.json",
    "outputs/g0/windows-evidence.json",
    PILOT_G0_EVIDENCE,
)
"""The G0 evidence an attestation hashes, where present: the outputs/g0 evidence the G0-01 and
G0-02/04/05 resolutions cite, and outputs/pilot/g0_evidence.json once T1-09 writes it. The
decisions record (docs/ralph/DECISIONS.md) is named in the attestation statement but not hashed,
because it is appended to throughout the event."""

G6_SNAPSHOT_SCOPE: Final = (
    "G6 for bundle {v} is evaluated after assembly by make validate and recorded in "
    "outputs/validation.json of the repository"
)


class GateError(Exception):
    """A gate source or record that fails a check; the message names the file."""


def canonical_json(value: object) -> bytes:
    """Sorted keys, 2-space indent, LF, trailing newline, no NaN (export.canonical_json)."""
    return provenance.canonical_json(value)


def not_run(gate_id: str, scope: str | None = None) -> GateRecord:
    """The NOT_RUN record of a gate without a source (PRD 9: every gate starts NOT_RUN)."""
    return _validate(
        {
            "gate_id": gate_id,
            "status": "NOT_RUN",
            "evidence_path": None,
            "run_id": None,
            "scope": scope,
            "result": None,
            "consequence": None,
            "command": None,
            "config_version": None,
            "input_hashes": [],
        },
        f"NOT_RUN {gate_id}",
    )


def _validate(document: object, where: str) -> GateRecord:
    try:
        return GateRecord.model_validate_json(json.dumps(document, allow_nan=False))
    except (ValidationError, ValueError) as error:
        raise GateError(f"{where}: not a valid GateRecord: {error}") from error


def _dump(record: GateRecord) -> dict[str, Any]:
    dumped: dict[str, Any] = record.model_dump(mode="json")
    return dumped


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise GateError(f"{path}: cannot read JSON: {error}") from error


def write_fragment(record: GateRecord, *, root: Path = REPOSITORY_ROOT) -> Path:
    """Validate ``record`` and write ``outputs/gates/<gate_id>.json`` in canonical JSON.

    Only that gate's file is written. Writing the same gate twice leaves the last record.
    """
    checked = _validate(_dump(record), f"fragment {record.gate_id}")
    target = root / GATES_DIR / f"{checked.gate_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json(_dump(checked)))
    return target


# --- merge ------------------------------------------------------------------------------------


def _config_mode(root: Path) -> str | None:
    document = yaml.safe_load((root / CONFIG_PATH).read_text(encoding="utf-8"))
    run = document.get("run") if isinstance(document, dict) else None
    mode = run.get("shipping_mode") if isinstance(run, dict) else None
    return mode if isinstance(mode, str) else None


def _evidence_problems(record: GateRecord, root: Path, where: str) -> list[str]:
    problems: list[str] = []
    path = record.evidence_path
    if path is None:
        return problems
    posix = PurePosixPath(path)
    if posix.is_relative_to(MOCK_BUNDLE_DIR):
        problems.append(f"{where}: evidence {path} lies under {MOCK_BUNDLE_DIR}")
    target = root / path
    if target.suffix == ".json" and target.is_file():
        document = _read_json(target)
        if isinstance(document, dict) and document.get("mock") is True:
            problems.append(f"{where}: evidence {path} carries mock true")
    return problems


def _attestation_problems(root: Path) -> list[str]:
    """Check outputs/gates/G0_attestation.json against the files it hashes and the config."""
    path = root / G0_ATTESTATION
    if not path.is_file():
        return [f"{G0_ATTESTATION}: missing, so a G0 PASS has no attestation"]
    document = _read_json(path)
    if not isinstance(document, dict):
        return [f"{G0_ATTESTATION}: not an object"]
    problems: list[str] = []
    mode = document.get("mode")
    configured = _config_mode(root)
    if mode != configured:
        problems.append(
            f"{G0_ATTESTATION}: attested mode {mode!r} differs from config run.shipping_mode "
            f"{configured!r}"
        )
    hashes = document.get("input_hashes")
    if not isinstance(hashes, list) or not hashes:
        problems.append(f"{G0_ATTESTATION}: no input_hashes")
        return problems
    for entry in hashes:
        if not isinstance(entry, dict):
            problems.append(f"{G0_ATTESTATION}: malformed input hash {entry!r}")
            continue
        problems.extend(_hash_problem("G0 attestation", root, entry))
    return problems


def _hash_problem(gate: str, root: Path, entry: dict[str, Any]) -> list[str]:
    name = entry.get("path")
    expected = entry.get("sha256")
    if not isinstance(name, str):
        return [f"{gate}: input hash without a path"]
    target = root / name
    if not target.is_file():
        return [f"{gate}: input {name} is missing"]
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        return [f"{gate}: input {name} changed (sha256 {actual}, recorded {expected})"]
    return []


@dataclass(frozen=True)
class _Source:
    gate_id: str
    path: str | None
    record: GateRecord


def _g0_source(root: Path) -> _Source:
    fragment = root / GATES_DIR / "G0.json"
    if fragment.is_file():
        record = _validate(_read_json(fragment), f"{GATES_DIR}/G0.json")
        return _Source("G0", f"{GATES_DIR}/G0.json", record)
    pilot = root / PILOT_G0_EVIDENCE
    if pilot.is_file():
        document = _read_json(pilot)
        block = document.get("gate_record") if isinstance(document, dict) else None
        if block is not None:
            record = _validate(block, f"{PILOT_G0_EVIDENCE} gate_record")
            if record.status not in ("NOT_RUN", "PARTIAL"):
                raise GateError(
                    f"{PILOT_G0_EVIDENCE}: the pilot block shows {record.status}; only a human "
                    "attestation (gates attest-g0) can record a G0 PASS"
                )
            return _Source("G0", PILOT_G0_EVIDENCE, record)
    return _Source("G0", None, not_run("G0"))


def _sources(root: Path) -> list[_Source]:
    gates_dir = root / GATES_DIR
    if gates_dir.is_dir():
        for path in sorted(gates_dir.glob("G*.json")):
            if path.stem in GATE_IDS:
                record = _validate(_read_json(path), f"{GATES_DIR}/{path.name}")
                if record.gate_id != path.stem:
                    raise GateError(
                        f"{GATES_DIR}/{path.name}: gate_id {record.gate_id} differs from the "
                        "file name"
                    )
    sources = [_g0_source(root)]
    g1 = root / G1_RECORD
    if g1.is_file():
        record = _validate(_read_json(g1), G1_RECORD)
        if record.gate_id != "G1":
            raise GateError(f"{G1_RECORD}: gate_id {record.gate_id} is not G1")
        sources.append(_Source("G1", G1_RECORD, record))
    else:
        sources.append(_Source("G1", None, not_run("G1")))
    for gate_id in GATE_IDS[2:]:
        path = gates_dir / f"{gate_id}.json"
        if path.is_file():
            record = _validate(_read_json(path), f"{GATES_DIR}/{gate_id}.json")
            sources.append(_Source(gate_id, f"{GATES_DIR}/{gate_id}.json", record))
        else:
            sources.append(_Source(gate_id, None, not_run(gate_id)))
    return sources


def merged_records(root: Path = REPOSITORY_ROOT) -> list[GateRecord]:
    """The seven GateRecords merge writes, in G0 to G6 order. Raises GateError on any check."""
    sources = _sources(root)
    seen = [source.record.gate_id for source in sources]
    if seen != list(GATE_IDS):
        raise GateError(f"gates out of order or duplicated: {seen}")
    problems: list[str] = []
    for source in sources:
        where = source.path or f"NOT_RUN {source.gate_id}"
        problems.extend(_evidence_problems(source.record, root, where))
    g0 = sources[0].record
    if g0.status == "PASS":
        problems.extend(_attestation_problems(root))
    if problems:
        raise GateError("; ".join(problems))
    return [source.record for source in sources]


def merge_bytes(root: Path = REPOSITORY_ROOT) -> bytes:
    """The bytes of outputs/validation.json."""
    return canonical_json([_dump(record) for record in merged_records(root)])


def merge(root: Path = REPOSITORY_ROOT) -> Path:
    """Write outputs/validation.json from the gate sources."""
    data = merge_bytes(root)
    target = root / VALIDATION_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


# --- verify -----------------------------------------------------------------------------------


def verify(root: Path = REPOSITORY_ROOT) -> list[str]:
    """Offline checks; returns the problems found and writes nothing."""
    problems: list[str] = []
    try:
        records = [source.record for source in _sources(root)]
    except GateError as error:
        return [str(error)]
    try:
        expected: bytes | None = merge_bytes(root)
    except GateError as error:
        problems.append(str(error))
        expected = None
    committed = root / VALIDATION_PATH
    if not committed.is_file():
        problems.append(f"{VALIDATION_PATH}: missing; run gates merge")
    elif expected is not None and committed.read_bytes() != expected:
        problems.append(f"{VALIDATION_PATH}: differs from the merge output; run gates merge")
    config_sha = provenance.config_version(root / CONFIG_PATH)
    for record in records:
        for entry in record.input_hashes:
            problems.extend(_hash_problem(record.gate_id, root, entry.model_dump(mode="json")))
        if record.config_version is not None and record.config_version != config_sha:
            problems.append(
                f"{record.gate_id}: stale_run_id: config_version {record.config_version} differs "
                f"from the current config sha256 {config_sha}"
            )
    if (root / GATES_DIR / "G0.json").is_file():
        problems.extend(_attestation_problems(root))
    return problems


# --- snapshot ---------------------------------------------------------------------------------


def snapshot_bytes(bundle_version: str, root: Path = REPOSITORY_ROOT) -> bytes:
    """validation.json for bundle ``bundle_version``: G0 to G5 from outputs/validation.json
    unchanged, G6 NOT_RUN for this bundle (CONTRACTS c.3)."""
    source = root / VALIDATION_PATH
    document = _read_json(source)
    if not isinstance(document, list) or len(document) != len(GATE_IDS):
        raise GateError(f"{VALIDATION_PATH}: not seven GateRecords")
    entries: list[dict[str, Any]] = []
    for gate_id, entry in zip(GATE_IDS[:6], document[:6], strict=True):
        record = _validate(entry, f"{VALIDATION_PATH} {gate_id}")
        if record.gate_id != gate_id:
            raise GateError(f"{VALIDATION_PATH}: entry {gate_id} holds {record.gate_id}")
        entries.append(_dump(record))
    entries.append(_dump(not_run("G6", G6_SNAPSHOT_SCOPE.format(v=bundle_version))))
    return canonical_json(entries)


# --- attest-g0 --------------------------------------------------------------------------------


def attest_g0(mode: str, operator: str, *, root: Path = REPOSITORY_ROOT) -> GateRecord:
    """Record the G0 decision: the attestation file and the PASS fragment. Never writes config."""
    configured = _config_mode(root)
    if configured is None:
        raise GateError("config run.shipping_mode is null; a person sets it before attest-g0")
    if mode != configured:
        raise GateError(f"attested mode {mode} differs from config run.shipping_mode {configured}")
    files = [name for name in G0_EVIDENCE_FILES if (root / name).is_file()]
    if not files:
        raise GateError("no G0 evidence file exists, so there is nothing to attest")
    inputs = [*files, CONFIG_PATH]
    hashes = sorted(
        ({"path": name, "sha256": provenance.config_version(root / name)} for name in inputs),
        key=lambda item: item["path"],
    )
    attestation = {
        "gate_id": "G0",
        "mode": mode,
        "operator": operator,
        "statement": (
            "G0 decision recorded with gates attest-g0; the resolutions it records are "
            "docs/ralph/DECISIONS.md G0-01-RESULT and G0-02/04/05-RESULT"
        ),
        "input_hashes": hashes,
        "config_sha256": provenance.config_version(root / CONFIG_PATH),
    }
    target = root / G0_ATTESTATION
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json(attestation))
    all_hashes = sorted(
        [*hashes, {"path": G0_ATTESTATION, "sha256": provenance.config_version(target)}],
        key=lambda item: item["path"],
    )
    record = _validate(
        {
            "gate_id": "G0",
            "status": "PASS",
            "evidence_path": G0_ATTESTATION,
            "run_id": provenance.run_id(
                root / CONFIG_PATH, [(item["path"], item["sha256"]) for item in hashes]
            ),
            "scope": (
                "G0 pilot decision (PRD 9): shipping mode, frozen target window, Earth search "
                "regions and the first-card Earth window, from the pilot evidence"
            ),
            "result": f"shipping mode {mode} attested by {operator}",
            "consequence": f"the bundle ships in Mode {mode} (PRD 4)",
            "command": provenance.command_line(
                MODULE,
                ["attest-g0", "--mode", mode, "--operator", operator, "--confirm", CONFIRM_G0],
            ),
            "config_version": provenance.config_version(root / CONFIG_PATH),
            "input_hashes": all_hashes,
        },
        "G0 attestation",
    )
    write_fragment(record, root=root)
    return record


# --- command line -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"python -m {MODULE}", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("merge", help="write outputs/validation.json from the gate sources")
    commands.add_parser("verify", help="offline checks; writes nothing")
    attest = commands.add_parser("attest-g0", help="record the G0 decision (not for automation)")
    attest.add_argument("--mode", choices=["A", "B", "C"], required=True)
    attest.add_argument("--operator", required=True)
    attest.add_argument("--confirm", required=True)
    snap = commands.add_parser("snapshot", help="validation.json for a bundle (CONTRACTS c.3)")
    snap.add_argument("--bundle-version", required=True)
    snap.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    root: Path = args.root

    try:
        if args.command == "merge":
            print(f"wrote {merge(root)}")
            return 0
        if args.command == "verify":
            problems = verify(root)
            for problem in problems:
                print(problem, file=sys.stderr)
            if problems:
                return 1
            print("gates verify: outputs/validation.json matches its sources")
            return 0
        if args.command == "attest-g0":
            if args.confirm != CONFIRM_G0:
                print(f"attest-g0 needs --confirm {CONFIRM_G0}; nothing written", file=sys.stderr)
                return 2
            record = attest_g0(args.mode, args.operator, root=root)
            print(f"recorded G0 {record.status} for mode {args.mode}; now run gates merge")
            return 0
        data = snapshot_bytes(args.bundle_version, root)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(data)
        print(f"wrote {args.out}")
        return 0
    except GateError as error:
        print(f"gates {args.command}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
