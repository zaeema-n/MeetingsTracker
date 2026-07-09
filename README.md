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

Each ministry folder contains four YAML files and optional JSON metadata sidecars:

```text
data/<Ministry name>/
  acts.yaml
  organisations.yaml
  meetings.yaml
  rtis.yaml
  act_metadata.json          # optional
  meeting_metadata.json      # optional
  *_metadata.json            # any future sidecar files
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

### Metadata sidecar files

Rich document data can live in `*_metadata.json` sidecar files alongside the YAML pack. The loader discovers every file matching `*_metadata.json` in the ministry folder (e.g. `act_metadata.json`, `meeting_metadata.json`) — no config changes needed for new sidecars.

**Envelope** (matches OpenGIN `EntityCreate.metadata`):

```json
{
  "entity_key": "<openGIN entity id>",
  "metadata": [
    { "key": "<metadata key>", "value": <any JSON> }
  ]
}
```

| File | `entity_key` | Metadata keys | Target kind |
|------|--------------|---------------|-------------|
| `act_metadata.json` | `cbsl_act_2023` | `related_documents`, `content` | `Document/act` |
| `meeting_metadata.json` | `governing_board_meeting` | `event_instances` | `Event/meeting` |

**`entity_key`** is the OpenGIN entity id. For create-path entities (acts, meetings, boards, etc.) this is the same as the pack `id` in the YAML files — no resolve step.

`node_id` references inside act `content` (e.g. `cbsl_governing_board`, `governing_board_meeting`) are semantic links within the MongoDB metadata document. They do **not** create graph edges; graph structure still comes from YAML (`mandated_by`, org tree, etc.).

Metadata ingest is **decoupled from graph ingest**: it can run after graph in the same CLI invocation (default), standalone on pre-existing nodes (`--metadata-only`), or be skipped (`--graph-only`). Recommended order: graph first, then metadata.

## Run ingest

`--active-at` is **required for graph ingest** (and optional for `--metadata-only`). It sets:

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

### Live ingest (graph + metadata)

Default run: graph ingest, then metadata sidecars.

```bash
python -m ingestion.cli.ingest_pack \
  "data/Minister of Finance, Planning and Economic Development" \
  --active-at 2026-06-12
```

### Metadata only

Use when graph nodes already exist in OpenGIN. `--active-at` is not required.

```bash
python -m ingestion.cli.ingest_pack \
  "data/Minister of Finance, Planning and Economic Development" \
  --metadata-only
```

### Graph only

Skip the metadata sidecar phase:

```bash
python -m ingestion.cli.ingest_pack \
  "data/Minister of Finance, Planning and Economic Development" \
  --active-at 2026-06-12 \
  --graph-only
```

### CLI flags

| Flag | Required | Description |
|------|----------|-------------|
| `pack_dir` | yes | Path to the ministry data folder |
| `--active-at DATE` | yes* | ISO date (e.g. `2024-11-01`) for resolve + create timestamps |
| `--schema PATH` | no | Pack schema YAML (default: `schema/pack_schema.yaml`) |
| `--dry-run` | no | Log what would happen; no writes to OpenGIN (both phases) |
| `--strict` | no | Fail if any create-path entity already exists (graph phase only) |
| `--metadata-only` | no | Skip graph ingest; only process `*_metadata.json` sidecars |
| `--graph-only` | no | Run graph ingest only; skip metadata sidecars |

\* Not required when `--metadata-only` is set.

### Help

```bash
python -m ingestion.cli.ingest_pack --help
```

## What the ingest does

### Graph phase (`--graph-only` or default)

1. **Load** YAML files into ingest records using `schema/pack_schema.yaml`.
2. **Resolve** the org tree marked `ingest: resolve`:
   - **Government** (`Organisation`/`government`) — by name only (root; no date filter)
   - **President, ministry, department** — via parent relationships at `--active-at`
3. **Create** other entities (acts, meetings, boards, RTIs, etc.) if they do not already exist in OpenGIN.
4. **Attach parent edges** (e.g. department `AS_BODY` → board) via parent `update_entity` calls after each create. Only the edge matching YAML nesting is created (one parent per nested record).

Create-path entities are matched by pack `id` + OpenGIN `kind`. If an entity already exists, it is skipped (unless `--strict` is set).

### Metadata phase (`--metadata-only` or default)

1. **Load** all `*_metadata.json` sidecar files from the pack directory.
2. **Verify** each `entity_key` exists in OpenGIN (id-only search).
3. **Update** entity metadata via `PUT /entities/{id}` with the sidecar payload. Entities not found are skipped with a warning.

Metadata does not require the entity to have been created in the current run — it works on any pre-existing node with a matching id.

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
[INFO] Graph ingest totals (active_at=2024-11-01, dry_run=True, strict=False)
[INFO]   resolved: 4
[INFO]   created: 0
[INFO]   skipped_existing: 1
[INFO]   dry_run_would_create: 10
[INFO] [DRY-RUN] Would update metadata cbsl_act_2023 from act_metadata.json (2 keys)
[INFO] Metadata ingest totals (dry_run=True)
[INFO]   dry_run_would_update_metadata: 2
[INFO]   skipped_not_found: 0
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
