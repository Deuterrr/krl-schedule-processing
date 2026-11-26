"""Export validated per-line KRL datasets and a build manifest to JSON."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from krl_schedule.models import BuildManifest, DatasetManifestEntry, LineDataset

logger = logging.getLogger(__name__)


def export_json(
    datasets: list[LineDataset],
    output_dir: Path,
    source_hashes: dict[str, str],
) -> BuildManifest:
    """Write per-line timetable JSON files and manifest.json into output_dir.

    Args:
        datasets: Validated LineDataset objects to serialize.
        output_dir: Target folder (created automatically if missing).
        source_hashes: Mapping of input filename -> SHA-256 digest for traceability.

    Returns:
        BuildManifest summarizing all written dataset files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    entries: list[DatasetManifestEntry] = []
    used_names: dict[str, int] = {}

    for dataset in datasets:
        base = f"{dataset.line}_{dataset.direction}"
        count = used_names.get(base, 0) + 1
        used_names[base] = count
        filename = f"{base}.json" if count == 1 else f"{base}_{count}.json"

        out_path = output_dir / filename
        out_path.write_text(
            dataset.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.debug("Wrote %s (%d train(s))", filename, len(dataset.trains))
        entries.append(
            DatasetManifestEntry(
                line=dataset.line,
                direction=dataset.direction,
                file=filename,
                train_count=len(dataset.trains),
                source_file=dataset.source_file,
            )
        )

    manifest = BuildManifest(
        generated_at=datetime.now(UTC),
        source_hashes=source_hashes,
        datasets=entries,
    )
    (output_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Wrote %d JSON dataset(s) + manifest.json -> %s",
        len(entries),
        output_dir,
    )
    return manifest
