"""
Project-based persistence. Each JD search becomes its own project, stored as
a separate JSON file, so opening a new role never overwrites a previous one.
A lightweight index file tracks project metadata for the list view without
needing to load every project file just to show a list.
"""
import json
import os
import re
import time
import uuid

PROJECTS_DIR = os.path.join(os.path.dirname(__file__), "projects")
INDEX_FILE = os.path.join(PROJECTS_DIR, "index.json")


def _ensure_dir():
    os.makedirs(PROJECTS_DIR, exist_ok=True)


def _slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "role"


def _project_path(project_id):
    return os.path.join(PROJECTS_DIR, f"{project_id}.json")


def _load_index():
    _ensure_dir()
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r") as f:
            return json.load(f)
    return []


def _save_index(index):
    _ensure_dir()
    with open(INDEX_FILE, "w") as f:
        json.dump(index, f, indent=2)


def list_projects():
    """Returns lightweight project summaries, most recently updated first."""
    return sorted(_load_index(), key=lambda p: p.get("updated_at", 0), reverse=True)


def create_project(role_title, jd_text, criteria, hard_filters=None, glossary=None):
    """Creates a new, fully independent project and returns its id."""
    _ensure_dir()
    project_id = f"{_slugify(role_title)}-{uuid.uuid4().hex[:6]}"
    now = time.time()
    project_data = {
        "id": project_id,
        "role_title": role_title,
        "jd_text": jd_text,
        "criteria": criteria,
        "hard_filters": hard_filters or [],
        "glossary": glossary or [],
        "golden_profile": None,
        "candidates": [],
        "interview_guide": {},
        "created_at": now,
        "updated_at": now,
    }
    save_project(project_id, project_data)
    return project_id


def load_project(project_id):
    path = _project_path(project_id)
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def save_project(project_id, data):
    """Saves a project's full data and keeps the lightweight index in sync."""
    _ensure_dir()
    data["updated_at"] = time.time()
    with open(_project_path(project_id), "w") as f:
        json.dump(data, f, indent=2)

    index = [p for p in _load_index() if p["id"] != project_id]
    index.append({
        "id": project_id,
        "role_title": data.get("role_title", "Untitled role"),
        "candidate_count": len(data.get("candidates", [])),
        "created_at": data.get("created_at", data["updated_at"]),
        "updated_at": data["updated_at"],
    })
    _save_index(index)


def delete_project(project_id):
    path = _project_path(project_id)
    if os.path.exists(path):
        os.remove(path)
    _save_index([p for p in _load_index() if p["id"] != project_id])
