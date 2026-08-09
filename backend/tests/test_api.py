"""API contract tests against the real application, end to end."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def brief() -> dict:
    return {
        "requirements": {
            "plot": {"width_ft": 30, "length_ft": 45, "shape": "rectangle", "facing": "east"},
            "bhk": "3BHK",
            "rooms": [
                "living_room",
                "dining_room",
                "kitchen",
                "master_bedroom",
                "children_bedroom",
                "guest_bedroom",
            ],
            "bathrooms": {"attached_count": 2, "common_count": 1},
            "features": ["balcony", "parking"],
            "style": "modern",
            "notes": "",
        },
        "variants": 3,
        "seed": 42,
    }


# --- System ---------------------------------------------------------------
def test_health(client) -> None:
    assert client.get(f"{PREFIX}/health").json() == {"status": "ok"}


def test_status_reports_subsystems(client) -> None:
    body = client.get(f"{PREFIX}/status").json()
    assert body["knowledge_base"]["templates"] == 20
    assert body["images"]["strategy"] == "vector"
    assert "model" in body["llm"]


# --- Options --------------------------------------------------------------
def test_options_drive_every_wizard_control(client) -> None:
    body = client.get(f"{PREFIX}/options").json()
    assert len(body["bhk_types"]) == 4
    assert len(body["styles"]) == 4
    assert {r["value"] for r in body["rooms"]} >= {"living_room", "kitchen", "pooja_room"}
    assert body["plot_width_range"]["min"] < body["plot_width_range"]["max"]


def test_options_carry_a_default_size_for_every_room(client) -> None:
    """The wizard seeds its dimension steppers from this, so it must be complete."""
    body = client.get(f"{PREFIX}/options").json()
    defaults = body["room_defaults"]
    assert {r["value"] for r in body["rooms"]} == set(defaults)
    assert defaults["living_room"] == {"length_ft": 16, "width_ft": 14}
    assert body["room_dimension_range"]["min"] < body["room_dimension_range"]["max"]


# --- Templates ------------------------------------------------------------
def test_list_templates(client) -> None:
    body = client.get(f"{PREFIX}/templates").json()
    assert len(body) == 20


def test_filter_templates_by_bhk(client) -> None:
    body = client.get(f"{PREFIX}/templates", params={"bhk": "4BHK"}).json()
    assert body
    assert all(t["bhk"] == "4BHK" for t in body)


def test_get_one_template(client) -> None:
    body = client.get(f"{PREFIX}/templates/TPL-001").json()
    assert body["id"] == "TPL-001"
    assert body["rooms"]


def test_unknown_template_is_a_structured_404(client) -> None:
    response = client.get(f"{PREFIX}/templates/TPL-999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- Matching -------------------------------------------------------------
def test_match_previews_without_generating_images(client, brief) -> None:
    response = client.post(f"{PREFIX}/match", json=brief["requirements"])
    assert response.status_code == 200
    matches = response.json()
    assert len(matches) == 5
    assert matches[0]["rank"] == 1
    assert "plot_dimensions" in matches[0]["breakdown"]


# --- Generation -----------------------------------------------------------
@pytest.fixture(scope="module")
def generated(client, brief) -> dict:
    response = client.post(f"{PREFIX}/generate", json=brief)
    assert response.status_code == 201, response.text
    return response.json()


def test_generate_returns_the_requested_variants(generated) -> None:
    assert len(generated["layouts"]) == 3
    assert generated["session_id"]
    assert generated["requirement_summary"]


def test_generated_layouts_carry_full_metadata(generated) -> None:
    for layout in generated["layouts"]:
        assert layout["bhk"] == "3BHK"
        assert layout["plot_size_label"] == "30 x 45 ft"
        assert layout["built_up_sqft"] > 0
        assert layout["rooms"]
        assert layout["image_url"].startswith(f"{PREFIX}/images/")
        assert layout["source_template_id"].startswith("TPL-")
        assert 0.0 <= layout["match_score"] <= 1.0


def test_layouts_are_distinct_from_each_other(generated) -> None:
    signatures = {
        tuple(sorted((r["name"], r["x"], r["y"]) for r in layout["rooms"]))
        for layout in generated["layouts"]
    }
    assert len(signatures) == len(generated["layouts"])


def test_generated_images_are_served(client, generated) -> None:
    for layout in generated["layouts"]:
        response = client.get(layout["image_url"])
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert len(response.content) > 5_000


def test_session_can_be_reopened(client, generated) -> None:
    body = client.get(f"{PREFIX}/sessions/{generated['session_id']}").json()
    assert body["session_id"] == generated["session_id"]
    assert len(body["layouts"]) == len(generated["layouts"])


def test_layout_can_be_fetched_and_selected(client, generated) -> None:
    layout_id = generated["layouts"][0]["id"]
    assert client.get(f"{PREFIX}/layouts/{layout_id}").json()["id"] == layout_id
    assert client.post(f"{PREFIX}/layouts/{layout_id}/select").status_code == 200


def test_seed_makes_generation_reproducible(client, brief, generated) -> None:
    repeat = client.post(f"{PREFIX}/generate", json=brief).json()
    assert [layout["source_template_id"] for layout in repeat["layouts"]] == [
        layout["source_template_id"] for layout in generated["layouts"]
    ]
    assert [r["x"] for r in repeat["layouts"][0]["rooms"]] == [
        r["x"] for r in generated["layouts"][0]["rooms"]
    ]


# --- Validation and safety ------------------------------------------------
def test_oversized_plot_is_rejected(client, brief) -> None:
    payload = {**brief, "requirements": {**brief["requirements"]}}
    payload["requirements"]["plot"] = {**payload["requirements"]["plot"], "width_ft": 500}
    assert client.post(f"{PREFIX}/generate", json=payload).status_code == 422


def test_room_dimensions_are_accepted_and_honoured(client, brief) -> None:
    payload = {**brief, "requirements": {**brief["requirements"]}, "variants": 1}
    payload["requirements"]["room_dimensions"] = {
        "living_room": {"length_ft": 18, "width_ft": 14},
        "kitchen": {"length_ft": 10, "width_ft": 9},
    }
    response = client.post(f"{PREFIX}/generate", json=payload)
    assert response.status_code == 201

    rooms = response.json()["layouts"][0]["rooms"]
    living = sum(r["width"] * r["height"] for r in rooms if r["type"] == "living_room")
    # Exact placement is not promised - staying in the right neighbourhood is.
    assert 0.5 * 252 <= living <= 1.5 * 252


def test_an_impossible_room_size_is_rejected(client, brief) -> None:
    payload = {**brief, "requirements": {**brief["requirements"]}}
    payload["requirements"]["room_dimensions"] = {
        "living_room": {"length_ft": 400, "width_ft": 400}
    }
    assert client.post(f"{PREFIX}/generate", json=payload).status_code == 422


def test_unknown_room_is_rejected(client, brief) -> None:
    payload = {**brief, "requirements": {**brief["requirements"]}}
    payload["requirements"]["rooms"] = ["swimming_pool"]
    assert client.post(f"{PREFIX}/generate", json=payload).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/images/../../secret/file.png",
        "/images/abc/..%2F..%2Fetc%2Fpasswd.png",
        "/images/abc/notes.txt",
    ],
)
def test_image_route_refuses_path_traversal(client, path) -> None:
    response = client.get(f"{PREFIX}{path}")
    assert response.status_code in (404, 422)


def test_square_plot_is_normalised(client, brief) -> None:
    """The wizard's two sliders are free-form, so a square plot is squared up."""
    payload = {**brief, "requirements": {**brief["requirements"]}, "variants": 1}
    payload["requirements"]["plot"] = {
        "width_ft": 30,
        "length_ft": 45,
        "shape": "square",
        "facing": "any",
    }
    body = client.post(f"{PREFIX}/generate", json=payload).json()
    assert body["layouts"][0]["plot_width_ft"] == body["layouts"][0]["plot_length_ft"] == 30


# --- Vastu ----------------------------------------------------------------
def test_options_offer_the_vastu_principles(client) -> None:
    body = client.get(f"{PREFIX}/options").json()
    values = {p["value"] for p in body["vastu_principles"]}
    assert {"pooja_northeast", "kitchen_southeast", "master_bedroom_southwest"} <= values
    assert all(p["label"] and p["description"] for p in body["vastu_principles"])


def test_a_brief_without_vastu_generates_layouts_that_do_not_mention_it(generated) -> None:
    for layout in generated["layouts"]:
        assert layout["vastu_score"] is None
        assert layout["vastu_notes"] == []


def test_a_vastu_brief_is_scored_and_explained(client, brief) -> None:
    payload = {**brief, "requirements": {**brief["requirements"]}, "variants": 2}
    payload["requirements"]["rooms"] = [*brief["requirements"]["rooms"], "pooja_room"]
    payload["requirements"]["vastu"] = {
        "enabled": True,
        "principles": ["pooja_northeast", "kitchen_southeast", "master_bedroom_southwest"],
    }

    body = client.post(f"{PREFIX}/generate", json=payload).json()
    layouts = body["layouts"]
    assert len(layouts) == 2
    for layout in layouts:
        assert 0.0 <= layout["vastu_score"] <= 1.0
        assert len(layout["vastu_notes"]) == 3
    # The client is ranking on compliance, so the best plan leads the gallery.
    assert layouts[0]["vastu_score"] >= layouts[1]["vastu_score"]


def test_vastu_left_out_of_the_payload_is_simply_off(client, brief) -> None:
    """The field is new, so an older client must still be a valid request."""
    payload = {**brief, "variants": 1}
    assert "vastu" not in payload["requirements"]
    body = client.post(f"{PREFIX}/generate", json=payload).json()
    assert body["layouts"][0]["vastu_score"] is None


def test_an_unknown_vastu_principle_is_rejected(client, brief) -> None:
    payload = {**brief, "requirements": {**brief["requirements"]}, "variants": 1}
    payload["requirements"]["vastu"] = {"enabled": True, "principles": ["kitchen_in_the_moon"]}
    assert client.post(f"{PREFIX}/generate", json=payload).status_code == 422
