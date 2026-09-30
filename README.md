<p align="center">
  <img src="https://um-ihc-ca2i.github.io/amplifai-challenge/assets/img/logo-mini-light.svg" alt="AMPLIFAI" width="280"/>
</p>

# AMPLIFAI Slicer Annotation Module (v2)

3D Slicer extension used by radiologists to annotate multi-phase liver CT for the **[AMPLIFAI Challenge](https://um-ihc-ca2i.github.io/amplifai-challenge/)** (MICCAI 2026). The module supports case navigation, multi-phase viewing, LI-RADS–oriented workflow steps, lesion and feature segmentations, and export of labels and segmentations for the challenge pipeline.

**Challenge site:** [https://um-ihc-ca2i.github.io/amplifai-challenge/](https://um-ihc-ca2i.github.io/amplifai-challenge/)

## Requirements

- [3D Slicer](https://download.slicer.org/) (scripted module support; developed on Slicer 5.x)
- Multi-phase CT and optional precomputed segmentations laid out as described below

## Install in 3D Slicer

1. Clone this repository (or download and unpack it):

   ```bash
   git clone https://github.com/UM-IHC-CA2i/AMPLIFAI-slicer-module.git
   ```

2. Create a local config file (not tracked in git):

   ```bash
   cp config.example.json config.json
   ```

   Edit `config.json` so paths match your machine (see [Configuration](#configuration)).

3. Register the module path in Slicer:

   - **Edit → Application Settings → Modules → Additional module paths**
   - Add the **repository root** (the folder that contains `CustomPanel.py`)
   - Restart Slicer

4. Open the module: **Modules → Informatics → AMLIFAI v2** (loads from `CustomPanel.py`).

On startup the module selects itself automatically when possible.

## Configuration

Settings live in `config.json` next to `CustomPanel.py`. Paths may be absolute or relative to the directory containing `config.json`. Use `~` for home directories.

| Key | Description |
| --- | --- |
| `scan_root` | Root directory of multi-phase CT cases (one subfolder per case). |
| `mask_root` | Root directory of optional AI or reference segmentations, mirroring case IDs under this folder. Used when loading existing `.nrrd` / `.seg.nrrd` masks. |
| `seg_save_dir` | Where radiologist-drawn segmentations are saved (per-case subfolders). |
| `labels_csv` | CSV file aggregating completed case metadata, LI-RADS fields, and paths to saved segmentations. Created or updated when a case is finalized. |
| `v2_state_dir` | Directory for in-progress annotation state (`cases/*.json`). Defaults to `~/.amlifai/v2` if omitted. |

Example template: [`config.example.json`](config.example.json).

## Input data layout

### Multi-phase CT (`scan_root`)

Cases are discovered as subfolders whose names start with `CASE` (e.g. `CASE00001`). Numeric IDs are normalized to five digits (`1` → `CASE00001`).

Each case folder contains one NIfTI volume per phase. Phase indices used in code:

| Phase ID | Name |
| ---: | --- |
| 0 | Arterial |
| 1 | Venous |
| 2 | Delayed |
| 3 | Dry |

Accepted filenames for case `CASE00001`, phase `0`:

- `CASE00001_0.nii.gz` / `CASE00001_0.nii`
- `CASE00001_0_ct.nii.gz` / `CASE00001_0_ct.nii`

(Same pattern for phases `1`, `2`, `3`.)

Example tree:

```text
scan_root/
  CASE00001/
    CASE00001_0.nii.gz
    CASE00001_1.nii.gz
    CASE00001_2.nii.gz
    CASE00001_3.nii.gz
  CASE00002/
    ...
```

### Optional segmentations (`mask_root` or `seg_save_dir`)

Per case, files live in `mask_root/CASE00001/` (or under `seg_save_dir` when saving). The loader matches patterns such as:

- `{case_id}_{phaseName}_{feature}.seg.nrrd` (e.g. `CASE00001_Arterial_aphe.seg.nrrd`)
- `{case_id}_{feature}.seg.nrrd` for single-phase features (e.g. lesion, APHE)

Feature names include `lesion`, `aphe`, `washout`, and `capsule`, aligned with the AMPLIFAI annotation workflow.

### Labels export (`labels_csv`)

When a case is completed, a row is appended or updated with fields such as `case_id`, segmentation paths, LI-RADS-related annotations, and clinical flags. The module treats cases listed in this file as already completed for “unseen case” navigation.

## Repository layout

| Path | Role |
| --- | --- |
| `CustomPanel.py` | Slicer module entry point |
| `data/` | Config, CT loading, layouts |
| `ui/` | Panels and toolbar |
| `segmentation/` | Segmentation I/O and AI mask loading |
| `state/` | Per-case JSON state |
| `workflow/` | Annotation step logic |

## Citation & challenge

If you use this tool or the AMPLIFAI dataset, please cite the challenge and dataset preprint linked from the [official challenge website](https://um-ihc-ca2i.github.io/amplifai-challenge/).

## License

Contact the repository maintainers for licensing terms if not specified in this repository.
