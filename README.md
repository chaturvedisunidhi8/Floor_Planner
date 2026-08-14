# Floor Planner Agent

AI-powered floor planner. Users pick their requirements through a guided
interface — no prompt writing — and the agent analyses the brief, searches a
knowledge base of 20 digitised residential floor plans, and generates 3–4
realistic layout images from the closest matches.

<p align="center">
  <em>React + Tailwind · FastAPI · Llama 3.3 70B (Groq) · BAAI/bge-small-en-v1.5 · FAISS · PostgreSQL · FLUX.1-dev</em>
</p>

---

## How it works

```
User selects requirements  (guided wizard: dropdowns, checkboxes, radios, sliders)
        │
        ▼  structured JSON
FastAPI backend
        │
        ▼
Llama 3.3 70B (Groq)  ──▶  architectural brief: zoning, priorities, adjacencies
        │
        ▼
bge-small-en-v1.5  ──▶  384-d embedding  ──▶  FAISS search over 20 templates
        │
        ▼
Similarity scorer  ──▶  re-ranks on plot size, bedrooms, rooms, bathrooms,
        │                parking, balcony, style, layout compatibility
        ▼
Top 3–5 matching templates
        │
        ▼
Geometry engine  ──▶  adapts each template to the user's plot, applies a
        │              distinct variation operator, validates and repairs
        ▼
Prompt builder  ──▶  FLUX.1-dev  ──▶  4 floor plan images
        │
        ▼
Layout gallery  ──▶  compare, enlarge, download, select
```

### Why the images are correct, not just plausible

A diffusion model cannot honour "10 ft × 12 ft bedroom" and renders text as
gibberish. So layout generation is split in two:

1. **A geometry engine** computes the actual room rectangles and renders them as
   a crisp architectural drawing — real walls, door swings, window breaks,
   dimension lines, labelled rooms, title block.
2. **FLUX.1-dev** supplies the material and lighting treatment. The drawing's
   linework layer is then composited back on top at full opacity.

The result reads as a rendered architectural plan while every label and
dimension remains exactly what the engine computed. If FLUX is unavailable the
pipeline falls back to the vector render, so **a generation request never fails
because of the image host** — and the app works with no API keys at all.

Set `IMAGE_STRATEGY` to `hybrid` (default), `vector` (renderer only) or `flux`
(pure text-to-image).

---

## Quick start

### Docker (everything, including PostgreSQL)

```bash
cp .env.example .env   # add GROQ_API_KEY and HUGGINGFACE_API_KEY if you have them
docker compose up --build
```

App on <http://localhost:8080>, API docs on <http://localhost:8000/docs>.

### Local development

Backend:

```bash
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python scripts/author_templates.py   # writes data/templates/TPL-XXX.json
python scripts/seed_database.py      # loads the DB and builds the FAISS index
python uvicorn app.main:app --reload or remove python or python -m uvicorn app.main:app --reload --port 8001
```

Frontend, in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`, so open
<http://localhost:5173>.

Without `DATABASE_URL` the backend uses a local SQLite file, so PostgreSQL is
optional for development.

### Generate from the command line

```bash
python scripts/demo_generate.py --width 30 --length 45 --bhk 3BHK --style luxury
```

---

## Project layout

```
backend/
  app/
    core/           config, logging, domain exceptions
    schemas/        pydantic contracts (enums, requirements, template, layout)
    models/         SQLAlchemy ORM
    db/             engine, session, schema init
    repositories/   data access (JSON and SQL implementations of one protocol)
    ai/
      llm/          Groq client + requirement analyzer
      embeddings/   bge encoder (+ offline hashing fallback)
      retrieval/    FAISS store, similarity scorer, matcher
      prompting/    custom prompt builder
      imaging/      FLUX backends, hybrid pipeline
    geometry/       primitives, layout engine, validator, renderer
    services/       generation orchestrator
    api/v1/         routers
  data/templates/   the 20-template knowledge base
  scripts/          author_templates, seed_database, demo_generate
  tests/            185 tests
frontend/
  src/
    api/            typed client
    components/     ui primitives, wizard steps, results gallery
    hooks/          requirement state
    types/          API contracts
```

Dependencies point inward: `api → services → ai/geometry → schemas`. Nothing in
`geometry/` or `ai/` imports FastAPI, and no service constructs its own
collaborators — everything is injected in `api/deps.py`.

---

## The knowledge base

`Templates/` holds the source images. `scripts/author_templates.py` digitises
them into structured JSON: plot size, BHK type, room positions and dimensions in
feet, room relationships, style and metadata.

Templates 001–016 are traced from those reference plans; 017–020 are
architect-standard additions covering plot sizes the traced set leaves thin
(compact 1BHK, minimal 2BHK, traditional 3BHK with pooja, luxury 4BHK villa).
Every template is validated for overlaps and plot containment at build time and
again in the test suite.

To edit the library, change the room tables in `author_templates.py` and re-run
it, then `seed_database.py`.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Liveness |
| `GET` | `/api/v1/status` | Which subsystems are live (LLM, embeddings, FLUX, DB) |
| `GET` | `/api/v1/options` | Every wizard choice — drives the whole UI |
| `GET` | `/api/v1/templates` | List the knowledge base (filter by `bhk`, `style`) |
| `GET` | `/api/v1/templates/{id}` | One full template |
| `POST` | `/api/v1/match` | Score templates without generating images |
| `POST` | `/api/v1/generate` | Analyse, match, generate and persist layouts |
| `GET` | `/api/v1/sessions/{id}` | Re-open a previous result set |
| `GET` | `/api/v1/layouts/{id}` | One generated layout |
| `POST` | `/api/v1/layouts/{id}/select` | Record the user's pick |
| `GET` | `/api/v1/images/{session}/{file}` | Serve a rendered plan |

Interactive docs at `/docs`.

---

## Configuration

Every setting lives in `.env` (see `.env.example`); no module reads
`os.environ` directly. The ones that matter most:

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | *(empty)* | Empty means SQLite. Set for PostgreSQL. |
| `GROQ_API_KEY` | *(empty)* | Without it, requirement analysis uses the rule-based baseline. |
| `HUGGINGFACE_API_KEY` | *(empty)* | Without it, images come from the vector renderer. |
| `IMAGE_STRATEGY` | `hybrid` | `hybrid` \| `vector` \| `flux` |
| `IMAGE_PROVIDER` | `huggingface` | `huggingface` \| `replicate` \| `fal` \| `local` \| `none` |
| `TOP_K_TEMPLATES` | `5` | Templates retrieved per request |
| `VARIANTS_PER_REQUEST` | `4` | Layout images generated per request |

Switching image host is a config change: `providers.py` has adapters for
Hugging Face, Replicate, fal.ai and local `diffusers` behind one interface.

---

## Tests

```bash
cd backend
python -m pytest
```

435 tests, no credentials or network needed. They cover the knowledge base
(overlaps, containment, bedroom counts), the geometry engine (every template
against every BHK — validity, no overlaps, no unassigned floor, outdoor space on
an external wall, balanced proportions, determinism, distinct variations), the
access model and doors (adjacency graph, one modeled door per access edge,
door-graph walkability from the entrance, the strict validation gate), the
scorer, the prompt builder, the image pipeline's fallback behaviour, the API
contract including path-traversal defence, and the topology search (candidate
generation invariants, base-order preservation, never-worse-than-base, and the
search audit trail).

---

## Benchmarking

`backend/scripts/benchmark.py` runs both engines against a fixed corpus of briefs
(narrow/deep 1BHK–4BHK shapes plus two briefs that are provably infeasible) and
reports per-brief and corpus-wide metrics:

```bash
cd backend
python scripts/benchmark.py --engine both --variants 2 --report ../benchmarks/engine_comparison.json
```

Metrics include room-area and dimension error, connectivity (fraction of indoor
rooms reachable from circulation), door-connectivity (reachable *through the
modeled doors*), door-satisfied (fraction of access requirements that actually
received a door), overlap, coverage, corridor, wall-clock time, and
**feasibility** — the fraction of attempts whose verdict matched the corpus
label (an infeasible brief refused, a feasible brief built). The solver engine
holds 100% connectivity, 100% door-satisfied, and 100% feasibility on the
corpus; the legacy engine never says "no", so its feasibility sits at 80%.

### Topology search

The solver engine no longer solves a single arrangement per (brief, template).
For each brief it now generates several **candidate topologies** — the original
programme plus soft spatial-zoning variants (bedrooms vs the social core pushed
to opposite halves of the plot) and deterministic room-order permutations — gives
each candidate the solver budget, scores the survivors with the architectural
scorer and returns the best plan. The hard guarantees are untouched: every
candidate carries the *same* room set and the *same* access edge set (the
en-suite pairing is ranked by bedroom priority, not spec order, so it survives
permutation), every survivor runs the strict validation gate before it can win,
and the winner is always re-ordered back into the base programme's room order.

```bash
cd backend
python scripts/topology_report.py --candidates 3   # before/after in one run
python scripts/benchmark.py --engine solver --topology-candidates 3
```

The search is configurable in `app/core/config.py`:

* `topology_candidates` — how many candidates per brief (default 5; `1` exactly
  reproduces the pre-search engine).
* `topology_zoning` / `topology_permutations` — the two diversity sources.
* `topology_bias_weight` — the soft objective credit that steers a zoning
  candidate (default 20; never a hard constraint).

Every solver plan carries its own search audit trail (`plan.topology_search`):
one entry per candidate with its label, verdict (`feasible` / `pruned` /
`infeasible` / `timeout`), score for survivors, and the validation errors that
pruned a candidate. `scripts/topology_report.py` aggregates that trail into the
before/after milestone numbers (base score vs best score, unique topologies,
pruned count, unique layouts). The search multiplies the solver time by the
candidate count — the quality gain is measured, and the candidate count is the
knob that trades latency for diversity.

---

## Extending it

- **More templates** — add a room table to `author_templates.py` and re-run the
  two scripts. Nothing else changes.
- **A new image host** — implement `ImageBackend` in `ai/imaging/providers.py`
  and register it in `BACKENDS`.
- **A new requirement** — add the field to `schemas/requirements.py`, a scorer
  method in `ai/retrieval/scorer.py`, and an option in `api/v1/options.py`; the
  wizard picks it up automatically.
- **A different LLM** — `ai/llm/client.py` is the only file that knows about
  Groq.

---

## Limitations

- Single-storey ground floor plans only; no multi-floor or 3D output.
- Generated plans are design studies. Verify all dimensions with a licensed
  architect before building.
- On the Hugging Face serverless endpoint FLUX cold-starts, so the first
  generation after an idle period is slow; the pipeline retries and then falls
  back to the vector render.
