"""Step 3 of the specification: the agent's understanding of the request.

The LLM turns the wizard's selections into an architectural brief - zoning
intent, priority rooms, adjacency preferences and a natural-language search
query. Everything it produces is advisory: the deterministic analysis below is
always computed first and is what the geometry engine actually relies on, so a
missing or misbehaving LLM degrades quality rather than breaking the pipeline.
"""

from __future__ import annotations

from typing import Any

from app.ai.llm.client import GroqClient, get_llm_client
from app.core.logging import get_logger
from app.schemas.enums import RoomType
from app.schemas.requirements import FloorPlanRequirements

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a senior residential architect assisting a floor plan \
generator. You receive a client's structured requirements and return a concise \
architectural brief as JSON.

Return exactly this shape:
{
  "summary": "<one sentence describing the home to be designed>",
  "zoning": {
    "public": ["<room keys placed in the public zone>"],
    "private": ["<room keys placed in the sleeping zone>"],
    "service": ["<room keys in the service zone>"]
  },
  "priority_rooms": ["<up to 4 room keys that must get the best position/light>"],
  "adjacency_preferences": [
    {"a": "<room key>", "b": "<room key>", "reason": "<short reason>"}
  ],
  "search_query": "<one sentence optimised for semantic search over a library of floor plans>",
  "design_notes": ["<short, concrete architectural guidance>"]
}

Rules:
- Only use room keys from the ALLOWED_ROOMS list given by the user.
- Keep every string under 160 characters.
- 3 to 6 adjacency preferences. 2 to 4 design notes.
- Respect Indian residential conventions when a pooja room is requested.
- When Vastu principles are listed, let them drive the zoning and design notes.
- Output JSON only."""


#: Deterministic zoning used as the baseline and as the fallback.
_ZONES: dict[str, tuple[RoomType, ...]] = {
    "public": (
        RoomType.LIVING_ROOM,
        RoomType.DINING_ROOM,
        RoomType.FOYER,
        RoomType.BALCONY,
        RoomType.PARKING,
        RoomType.GARDEN,
        RoomType.STAIRCASE,
        RoomType.PASSAGE,
    ),
    "private": (
        RoomType.MASTER_BEDROOM,
        RoomType.GUEST_BEDROOM,
        RoomType.CHILDREN_BEDROOM,
        RoomType.BEDROOM,
        RoomType.STUDY_ROOM,
        RoomType.ATTACHED_BATHROOM,
        RoomType.POOJA_ROOM,
    ),
    "service": (
        RoomType.KITCHEN,
        RoomType.UTILITY_ROOM,
        RoomType.STORE_ROOM,
        RoomType.WASH_AREA,
        RoomType.COMMON_BATHROOM,
    ),
}

#: Adjacencies a residential plan should honour regardless of what the LLM says.
BASELINE_ADJACENCIES: tuple[tuple[RoomType, RoomType, str], ...] = (
    (RoomType.KITCHEN, RoomType.DINING_ROOM, "Serving distance between cooking and eating"),
    (RoomType.DINING_ROOM, RoomType.LIVING_ROOM, "Open social core"),
    (RoomType.KITCHEN, RoomType.UTILITY_ROOM, "Shared plumbing and service access"),
    (RoomType.MASTER_BEDROOM, RoomType.ATTACHED_BATHROOM, "Private en-suite"),
    (RoomType.LIVING_ROOM, RoomType.BALCONY, "Daylight and outdoor spill-out"),
    (RoomType.LIVING_ROOM, RoomType.FOYER, "Entry sequence"),
)


class RequirementAnalysis(dict):
    """Plain dict with typed accessors, so it serialises straight into the API."""

    @property
    def summary(self) -> str:
        return str(self.get("summary", ""))

    @property
    def search_query(self) -> str:
        return str(self.get("search_query", ""))

    @property
    def priority_rooms(self) -> list[RoomType]:
        return _coerce_rooms(self.get("priority_rooms", []))

    @property
    def zoning(self) -> dict[str, list[RoomType]]:
        raw = self.get("zoning", {})
        return {zone: _coerce_rooms(raw.get(zone, [])) for zone in ("public", "private", "service")}

    @property
    def adjacency_preferences(self) -> list[tuple[RoomType, RoomType]]:
        pairs: list[tuple[RoomType, RoomType]] = []
        for item in self.get("adjacency_preferences", []):
            if not isinstance(item, dict):
                continue
            a, b = _coerce_room(item.get("a")), _coerce_room(item.get("b"))
            if a and b and a is not b:
                pairs.append((a, b))
        return pairs

    @property
    def source(self) -> str:
        return str(self.get("source", "rule-based"))


class RequirementAnalyzer:
    def __init__(self, client: GroqClient | None = None) -> None:
        self._client = client or get_llm_client()

    def analyze(self, requirements: FloorPlanRequirements) -> RequirementAnalysis:
        baseline = self._baseline(requirements)

        payload = self._client.complete_json(
            SYSTEM_PROMPT,
            self._user_prompt(requirements),
            temperature=0.2,
        )
        if not payload:
            return baseline

        merged = self._merge(baseline, payload)
        merged["source"] = f"groq:{self._client.model}"
        logger.info("Requirement analysis enriched by %s", merged["source"])
        return merged

    # --- Deterministic baseline ------------------------------------------
    @staticmethod
    def _baseline(req: FloorPlanRequirements) -> RequirementAnalysis:
        present = set(req.all_room_types)
        zoning = {
            zone: [r.value for r in rooms if r in present] for zone, rooms in _ZONES.items()
        }

        priority = [
            r.value
            for r in (
                RoomType.LIVING_ROOM,
                RoomType.MASTER_BEDROOM,
                RoomType.KITCHEN,
                RoomType.DINING_ROOM,
            )
            if r in present
        ]

        adjacencies = [
            {"a": a.value, "b": b.value, "reason": reason}
            for a, b, reason in BASELINE_ADJACENCIES
            if a in present and b in present
        ]

        rooms = ", ".join(r.label for r in req.rooms)
        notes = [
            "Keep the sleeping zone away from the entry so circulation never crosses it.",
            "Place wet areas back-to-back to shorten plumbing runs.",
            "Give the living room the longest external wall for daylight.",
        ]
        if req.vastu.is_active:
            # Named first so it survives the four-note cap in _merge.
            notes.insert(0, req.vastu.describe().capitalize() + ".")

        return RequirementAnalysis(
            summary=(
                f"A {req.style.label.lower()} {req.bhk.value} home on a {req.plot.describe()} "
                f"with {rooms.lower()} and {req.bathrooms.describe()}."
            ),
            zoning=zoning,
            priority_rooms=priority,
            adjacency_preferences=adjacencies,
            search_query=req.to_search_text(),
            design_notes=notes,
            source="rule-based",
        )

    @staticmethod
    def _user_prompt(req: FloorPlanRequirements) -> str:
        allowed = ", ".join(sorted({r.value for r in req.all_room_types}))
        sizes = req.describe_dimensions()
        size_line = (
            f"- Client room sizes (length x width, preserve where possible): {sizes}\n"
            if sizes
            else ""
        )
        return (
            f"CLIENT REQUIREMENTS\n"
            f"- Plot: {req.plot.describe()}, facing {req.plot.facing.value}\n"
            f"- Configuration: {req.bhk.value}\n"
            f"- Rooms requested: {', '.join(r.value for r in req.rooms)}\n"
            f"{size_line}"
            f"- Bathrooms: {req.bathrooms.describe()}\n"
            f"- Additional features: {', '.join(f.value for f in req.features) or 'none'}\n"
            f"- Vastu: {req.vastu.describe()}\n"
            f"- Interior style: {req.style.value}\n"
            f"- Client notes: {req.notes or 'none'}\n\n"
            f"ALLOWED_ROOMS: [{allowed}]\n\n"
            "Produce the architectural brief JSON."
        )

    @staticmethod
    def _merge(baseline: RequirementAnalysis, payload: dict[str, Any]) -> RequirementAnalysis:
        """Take the LLM's judgement where it is well-formed, keep the baseline otherwise."""
        merged = RequirementAnalysis(baseline)

        if isinstance(payload.get("summary"), str) and payload["summary"].strip():
            merged["summary"] = payload["summary"].strip()[:400]

        if isinstance(payload.get("search_query"), str) and payload["search_query"].strip():
            merged["search_query"] = payload["search_query"].strip()[:400]

        zoning = payload.get("zoning")
        if isinstance(zoning, dict):
            for zone in ("public", "private", "service"):
                rooms = _coerce_rooms(zoning.get(zone, []))
                if rooms:
                    merged.setdefault("zoning", {})[zone] = [r.value for r in rooms]

        priority = _coerce_rooms(payload.get("priority_rooms", []))
        if priority:
            merged["priority_rooms"] = [r.value for r in priority[:4]]

        prefs = [
            item
            for item in payload.get("adjacency_preferences", [])
            if isinstance(item, dict)
            and _coerce_room(item.get("a"))
            and _coerce_room(item.get("b"))
        ]
        if prefs:
            # Baseline adjacencies are architectural non-negotiables: keep them
            # and append whatever extras the model proposed.
            existing = {(p["a"], p["b"]) for p in merged["adjacency_preferences"]}
            for item in prefs[:8]:
                key = (item["a"], item["b"])
                if key not in existing and (key[1], key[0]) not in existing:
                    merged["adjacency_preferences"].append(
                        {"a": key[0], "b": key[1], "reason": str(item.get("reason", ""))[:160]}
                    )

        notes = [str(n)[:200] for n in payload.get("design_notes", []) if isinstance(n, str)]
        if notes:
            merged["design_notes"] = notes[:4]

        return merged


def _coerce_room(value: Any) -> RoomType | None:
    if not isinstance(value, str):
        return None
    try:
        return RoomType(value.strip().lower())
    except ValueError:
        return None


def _coerce_rooms(values: Any) -> list[RoomType]:
    if not isinstance(values, list):
        return []
    rooms = [_coerce_room(v) for v in values]
    return [r for r in rooms if r is not None]
