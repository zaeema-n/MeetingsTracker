# MeetingsTracker

Ingest ministry data packs (YAML) into [OpenGIN](https://github.com/opengin) via the Read and Ingestion APIs.

## Prerequisites

- Python 3.10+
- OpenGIN Read API and Ingestion API reachable from your machine
- A ministry pack under `data/<Ministry name>/`

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Configure API URLs. Copy the env template and edit as needed:

```bash
cp ingestion/.env.template .env
```

`.env` example:

```env
READ_BASE_URL="http://localhost:8081"
INGESTION_BASE_URL="http://localhost:8080"
```

`python-dotenv` loads `.env` from the current working directory, so run ingest commands from the repo root (or export these variables in your shell).

## Pack layout

Each ministry folder contains four YAML files:

```text
data/<Ministry name>/
  acts.yaml
  organisations.yaml
  meetings.yaml
  rtis.yaml
```

Mapping rules live in the global schema (not per-ministry):

```text
schema/pack_schema.yaml
```

Ingestion is **schema-driven**: loader, resolve, and mapper read entity types, nesting, processing order, and relationships from this file. Adding a new entity type requires changes there only — not hardcoded Python lists.

### Schema conventions

**YAML key = entity type name.** Nested collection keys in pack files must match entity type names from the schema (e.g. `meeting_instance:`, not `instances:`). Root keys per file: `act`, `meeting`, `rti_document`, `government`, etc.

**`ingest_order`** in `pack_schema.yaml` lists every entity type exactly once. It controls processing order (resolve logging and create sequence), separate from YAML tree nesting.

**Adding a new entity type:**

1. Add an `entities.<type>` block in `schema/pack_schema.yaml` (`file`, `kind`, `default_ingest`, optional `parent_relationships`).
2. Append `<type>` to `ingest_order`.
3. Use `<type>:` as the nested YAML key under its parent in the pack data.

**`parent_relationships`** declare *allowed* tree parent edges. Multiple entries (e.g. board under department or ministry) are valid options — YAML nesting picks exactly one via `_tree_parent_type` at ingest time.

**Link field vs entity key collisions.** Cross-reference fields on a record (e.g. `meetings:` on a board) must not use the same name as a nested entity type key on that node. The entity type is `meeting`, so `meetings` as a link field is fine; avoid naming a link field `meeting` on a node that could nest `meeting` children.

Example pack:

```text
data/Minister of Finance, Planning and Economic Development/
```

## Run ingest

`--active-at` is **required on every run**. It sets:

- the date used to resolve president / ministry / department in OpenGIN
- timestamps on newly created entities and relationships (`created`, `startTime`)

### Dry run (recommended first)

Performs resolve lookups and existence checks, but does not create or update anything:

```bash
python -m ingestion.cli.ingest_pack \
  "data/Minister of Finance, Planning and Economic Development" \
  --active-at 2026-06-12 \
  --dry-run
```

### Live ingest

```bash
python -m ingestion.cli.ingest_pack \
  "data/Minister of Finance, Planning and Economic Development" \
  --active-at 2026-06-12
```

### CLI flags

| Flag | Required | Description |
|------|----------|-------------|
| `pack_dir` | yes | Path to the ministry data folder |
| `--active-at DATE` | yes | ISO date (e.g. `2024-11-01`) for resolve + create timestamps |
| `--schema PATH` | no | Pack schema YAML (default: `schema/pack_schema.yaml`) |
| `--dry-run` | no | Log what would happen; no writes to OpenGIN |
| `--strict` | no | Fail if any create-path entity already exists |

### Help

```bash
python -m ingestion.cli.ingest_pack --help
```

## What the ingest does

1. **Load** YAML files into ingest records using `schema/pack_schema.yaml`.
2. **Resolve** the org tree marked `ingest: resolve`:
   - **Government** (`Organisation`/`government`) — by name only (root; no date filter)
   - **President, ministry, department** — via parent relationships at `--active-at`
3. **Create** other entities (acts, meetings, boards, RTIs, etc.) if they do not already exist in OpenGIN.
4. **Attach parent edges** (e.g. department `AS_BODY` → board) via parent `update_entity` calls after each create. Only the edge matching YAML nesting is created (one parent per nested record).

Create-path entities are matched by pack `id` + OpenGIN `kind`. If an entity already exists, it is skipped (unless `--strict` is set).

### Collection file YAML shape

Acts, meetings, and RTIs use entity-type keys at the root and for nesting:

```yaml
# acts.yaml
act:
  - id: cbsl_act_2023
    name: ...

# meetings.yaml
meeting:
  - id: governing_board_meeting
    name: ...
    meeting_instance:
      - id: GBM_156
        name: ...
```

### Organisation YAML shape

```yaml
government:
  - name: Government of Sri Lanka
    ingest: resolve
    president:
      - name: Anura Kumara Dissanayake
        ingest: resolve
        ministry:
          - name: Minister of Finance, Planning and Economic Development
            ingest: resolve
            department:
              - name: Central Bank of Sri Lanka
                ingest: resolve
                board:
                  - id: cbsl_governing_board
                    name: CBSL Governing Board
```

## Example output

```text
[INFO] Starting ingest for ... (active_at=2024-11-01, dry_run=True, strict=False)
[INFO] [RESOLVE] government government[0] -> <db-id>
[INFO] [RESOLVE] president government[0].president[0] -> <db-id>
[INFO] [RESOLVE] ministry government[0].president[0].ministry[0] -> <db-id>
[INFO] [RESOLVE] department government[0].president[0].ministry[0].department[0] -> <db-id>
[INFO] [DRY-RUN] Would create Organisation/board cbsl_governing_board at ...
[INFO] [DRY-RUN] Would update parent <dept-id> AS_BODY -> cbsl_governing_board
[INFO] [SKIP] Document/Act cbsl_act_2023 at act[0] (already in DB)
[INFO] Ingest complete (active_at=2024-11-01, dry_run=True, strict=False)
[INFO]   resolved: 4
[INFO]   created: 0
[INFO]   skipped_existing: 1
[INFO]   dry_run_would_create: 10
```

## Troubleshooting

| Error | Likely cause |
|-------|----------------|
| `Ingest failed: None/v1/entities/search` | `READ_BASE_URL` not set — add `.env` or export the variable |
| `No government named '...' found at ...` | Government root not in OpenGIN (name/kind mismatch) |
| `No president named '...' found ... on <date>` | President not linked to government at that `--active-at`, or wrong date |
| `Ambiguous ministry named '...'` | Multiple matches — data or graph needs disambiguation |
| `... already exists in OpenGIN` with `--strict` | Entity already present; remove `--strict` to skip existing entities |
| `HTTP client not initialized` | Internal error — report if seen after a normal CLI run |

Resolve requires the government root in OpenGIN (by name), then president/ministry/department reachable via `AS_PRESIDENT` / `AS_MINISTER` / `AS_DEPARTMENT` at the given date. Create-path entities need stable pack `id` values that match OpenGIN entity ids when re-ingesting.
