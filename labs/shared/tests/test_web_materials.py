from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from docx import Document

import labs.shared.web.materials as materials_module
from labs.shared.web.materials import MaterialStore


WORKSPACE = "workspace_test123"


def test_defaults_work_with_only_lab_1_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "lab_1_workspace"
    lab_1_data = root / "labs" / "lab_01" / "data"
    lab_1_data.mkdir(parents=True)
    (lab_1_data / "profile.json").write_text('{"headline": "Python engineer"}', encoding="utf-8")
    (lab_1_data / "job_description.json").write_text('{"title": "AI Engineer"}', encoding="utf-8")
    monkeypatch.setattr(materials_module, "ROOT_DIR", root)

    defaults = MaterialStore(tmp_path / "materials").create_defaults(WORKSPACE)

    assert {item.kind for item in defaults} == {"candidate_profile", "job_description"}


def test_defaults_add_web_sources_only_after_lab_3_is_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "lab_3_workspace"
    lab_1_data = root / "labs" / "lab_01" / "data"
    lab_1_data.mkdir(parents=True)
    (lab_1_data / "profile.json").write_text('{"headline": "Python engineer"}', encoding="utf-8")
    (lab_1_data / "job_description.json").write_text('{"title": "AI Engineer"}', encoding="utf-8")
    lab_3_data = root / "labs" / "lab_03" / "data"
    lab_3_data.mkdir(parents=True)
    (lab_3_data / "sources.json").write_text('[{"snippet": "Public company information"}]', encoding="utf-8")
    monkeypatch.setattr(materials_module, "ROOT_DIR", root)

    defaults = MaterialStore(tmp_path / "materials").create_defaults(WORKSPACE)

    assert {item.kind for item in defaults} == {"candidate_profile", "job_description", "web_source"}


def test_defaults_upgrade_an_existing_lab_1_workspace_without_replacing_materials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "progressive_workspace"
    lab_1_data = root / "labs" / "lab_01" / "data"
    lab_1_data.mkdir(parents=True)
    (lab_1_data / "profile.json").write_text('{"headline": "Python engineer"}', encoding="utf-8")
    (lab_1_data / "job_description.json").write_text('{"title": "AI Engineer"}', encoding="utf-8")
    monkeypatch.setattr(materials_module, "ROOT_DIR", root)
    store = MaterialStore(tmp_path / "materials")
    lab_1_defaults = store.create_defaults(WORKSPACE)
    original_ids = {item.material_id for item in lab_1_defaults}

    lab_3_data = root / "labs" / "lab_03" / "data"
    lab_3_data.mkdir(parents=True)
    (lab_3_data / "sources.json").write_text('[{"snippet": "Public company information"}]', encoding="utf-8")
    upgraded = store.create_defaults(WORKSPACE)
    upgraded_again = store.create_defaults(WORKSPACE)

    assert original_ids < {item.material_id for item in upgraded}
    assert [item.kind for item in upgraded].count("web_source") == 1
    assert {item.material_id for item in upgraded_again} == {item.material_id for item in upgraded}


def test_text_materials_replace_single_profile_and_keep_sources(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path)

    first = store.create_text(WORKSPACE, "candidate_profile", "first.md", "Python engineer")
    second = store.create_text(WORKSPACE, "candidate_profile", "second.md", "AI product engineer")
    source = store.create_text(WORKSPACE, "web_source", "company.md", "Company builds developer tools")

    materials = store.list(WORKSPACE)
    assert first.material_id not in {item.material_id for item in materials}
    assert {item.material_id for item in materials} == {second.material_id, source.material_id}
    assert not (tmp_path / WORKSPACE / f"{first.material_id}.json").exists()


def test_upload_normalizes_in_memory_and_uses_safe_name(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path)

    item = store.create_upload(
        WORKSPACE,
        "job_description",
        "../../private/job.txt",
        "text/plain",
        b"AI Engineer\r\nPython required",
    )

    assert item.display_name == "job.txt"
    assert item.characters > 0
    files = list((tmp_path / WORKSPACE).iterdir())
    assert files == [tmp_path / WORKSPACE / f"{item.material_id}.json"]
    assert "Python required" in store.context(WORKSPACE)[0]["text"]


def test_docx_upload_extracts_text(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("Backend engineer with Python and Gemini API experience.")
    payload = io.BytesIO()
    document.save(payload)
    store = MaterialStore(tmp_path)

    item = store.create_upload(
        WORKSPACE,
        "candidate_profile",
        "resume.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        payload.getvalue(),
    )

    assert item.status == "ready"
    assert "Gemini API" in store.context(WORKSPACE)[0]["text"]


def test_invalid_pdf_and_mismatched_content_type_are_rejected(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path)

    with pytest.raises(ValueError, match="could not be parsed"):
        store.create_upload(WORKSPACE, "candidate_profile", "resume.pdf", "application/pdf", b"not-a-pdf")

    with pytest.raises(ValueError, match="content type"):
        store.create_upload(WORKSPACE, "candidate_profile", "resume.pdf", "text/plain", b"not-a-pdf")


def test_workspace_and_material_ids_cannot_traverse(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path)

    with pytest.raises(ValueError, match="workspace"):
        store.create_text("../../outside", "candidate_profile", "resume.md", "Python")

    store.create_text(WORKSPACE, "candidate_profile", "resume.md", "Python")
    with pytest.raises(ValueError, match="material"):
        store.delete(WORKSPACE, "../../outside")


def test_clear_removes_normalized_workspace(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path)
    store.create_text(WORKSPACE, "candidate_profile", "resume.md", "Python")
    (tmp_path / WORKSPACE / "stray.tmp").write_text("interrupted local write", encoding="utf-8")

    store.clear(WORKSPACE)

    assert store.list(WORKSPACE) == []
    assert not (tmp_path / WORKSPACE).exists()


def test_web_source_limit_is_enforced_without_dropping_existing_sources(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path)
    for index in range(10):
        store.create_text(
            WORKSPACE,
            "web_source",
            f"source-{index}.md",
            f"Source evidence {index}",
        )

    with pytest.raises(ValueError, match="at most 10"):
        store.create_text(WORKSPACE, "web_source", "source-10.md", "One source too many")

    assert len(store.list(WORKSPACE)) == 10


def test_web_source_stays_pending_until_lab_3_fetch_completes_it(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path)

    pending = store.create_url(WORKSPACE, "https://example.com/jobs/ai-tools#apply")
    duplicate = store.create_url(WORKSPACE, "https://example.com/jobs/ai-tools")

    assert duplicate.material_id == pending.material_id
    assert pending.kind == "web_source"
    assert pending.source == "web"
    assert pending.status == "pending"
    assert pending.characters == 0
    assert pending.source_url == "https://example.com/jobs/ai-tools"

    ready = store.complete_web_source(
        WORKSPACE,
        pending.material_id,
        title="AI Tools Engineer — Northstar",
        final_url="https://example.com/jobs/ai-tools",
        text="The role requires Python API integration experience.",
    )

    assert ready.status == "ready"
    assert ready.display_name == "AI Tools Engineer — Northstar"
    assert ready.characters > 0
    assert "Python API" in store.context(WORKSPACE)[0]["text"]


def test_legacy_source_record_is_migrated_to_explicit_web_source_kind(tmp_path: Path) -> None:
    store = MaterialStore(tmp_path / "materials")
    workspace = store.root / WORKSPACE
    workspace.mkdir(parents=True)
    record_path = workspace / "mat_legacy1234.json"
    record_path.write_text(
        json.dumps(
            {
                "material_id": "mat_legacy1234",
                "kind": "supporting_source",
                "display_name": "Old company page",
                "text": "A saved public webpage.",
                "characters": 23,
                "source": "fixture",
                "status": "ready",
                "source_url": None,
                "created_at": "2026-07-11T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    [record] = store.list(WORKSPACE)

    assert record.kind == "web_source"
    assert json.loads(record_path.read_text(encoding="utf-8"))["kind"] == "web_source"
