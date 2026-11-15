"""Project-scoped component feedback and candidate hardware snapshots."""
from copy import deepcopy
import json
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from webapp.engineering_workflow import (baseline_snapshot, changed_parameters,
    component_definition, component_patch, fingerprint, numerical_evidence, workflow_catalog)


class ComponentFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=1)
    source_job_id: str = Field(min_length=1, max_length=100)
    component: str = Field(min_length=1, max_length=40)
    patch: dict = Field(min_length=1, max_length=200)


class CandidateBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_job_id: str = Field(min_length=1, max_length=100)


def register_engineering_routes(app, store, session_user, get_project, fixed_source, body):
    from lre_workbench.domain.models import ValidationError
    from webapp.solver import input_values
    from webapp.ui_schema import design_ui_schema
    from webapp.design_scope import require_supported_design, require_supported_source

    def decoded(rows, field):
        for row in rows:
            row[field] = json.loads(row[field])
        return rows

    @app.get("/api/projects/{project_id}/engineering")
    def engineering(project_id: str, request: Request):
        session_user(request)
        project = get_project(project_id)
        iterations = decoded(store.all(
            "SELECT * FROM component_iterations WHERE project_id=? ORDER BY created DESC LIMIT 100",
            (project_id,)), "snapshot")
        jobs = store.all("SELECT id,revision,inputs FROM jobs WHERE project_id=? AND kind='design' "
                         "AND status='succeeded' ORDER BY created DESC", (project_id,))
        for job in jobs:
            job["inputs"] = json.loads(job["inputs"])
            job["inputs_sha256"] = fingerprint(job["inputs"])
        numerical_cache = {}
        for iteration in iterations:
            target = iteration["snapshot"]["target_inputs_sha256"]
            changes = iteration["snapshot"]["changes"]
            # Several component edits may be integrated in one later run.
            # Keep the exact-target distinction, but don't require an obsolete
            # intermediate revision to be rerun when its edits are all present.
            match = next((job for job in jobs if job["revision"] >= iteration["target_revision"]
                          and all(change["key"] in job["inputs"] and
                                  job["inputs"][change["key"]] == change["after"] for change in changes)), None)
            iteration["review_job_id"] = match["id"] if match else None
            iteration["status"] = ("recomputed" if match["inputs_sha256"] == target else
                                   "recomputed_with_changes") if match else "awaiting_recompute"
            if match and match["id"] not in numerical_cache:
                saved = store.one("SELECT result FROM jobs WHERE id=? AND project_id=?", (match["id"], project_id))
                numerical_cache[match["id"]] = numerical_evidence(json.loads(saved["result"]) if saved and saved["result"] else {})
            iteration["numerical_review"] = numerical_cache[match["id"]] if match else None
        baselines = decoded(store.all(
            "SELECT * FROM hardware_baselines WHERE project_id=? ORDER BY created DESC LIMIT 100",
            (project_id,)), "snapshot")
        for row in baselines:
            row["integrity"] = fingerprint(row["snapshot"]["payload"]) == row["snapshot"]["sha256"]
        return {"catalog": workflow_catalog(), "revision": project["revision"],
                "iterations": iterations, "baselines": baselines,
                "capabilities": {"nominal_hardware_verification": False,
                                 "fixed_hardware_steady": False,
                                 "transient": "bounded_research"}}

    @app.post("/api/projects/{project_id}/engineering/iterations")
    async def feedback(project_id: str, request: Request):
        user = session_user(request)
        data = await body(request, ComponentFeedback)
        project = get_project(project_id)
        source = fixed_source(project_id, data.source_job_id)
        try:
            require_supported_design(project["inputs"])
            require_supported_source(source)
            proposed, changes = component_patch(data.component, data.patch, project["inputs"],
                                                design_ui_schema()["system_groups"], allow_unchanged=True)
            values, warnings = input_values(proposed)
            changes = changed_parameters(project["inputs"], values)
            if set(changes) - set(data.patch):
                raise ValueError("本次修改需要同时规范其他输入；请先在总体输入中保存新版本，再记录部件反馈。")
            source_changes = [key for key in sorted(data.patch) if source["inputs"].get(key) != values.get(key)]
            if not changes and not source_changes:
                raise ValueError("本部件与来源记录相同，没有待反馈的修改。")
            component = component_definition(data.component)
            snapshot = {"schema_version": 1, "component": data.component,
                "component_label": component["label"], "source_job_id": source["id"],
                "source_revision": source["revision"], "source_result_sha256": fingerprint(source["result"]),
                "changes": [{"key": key, "before": deepcopy(source["inputs"].get(key)),
                             "after": deepcopy(values.get(key))} for key in sorted(set(changes) | set(source_changes))],
                "saved_parameter_changes": changes,
                "previous_saved_values": {key: deepcopy(project["inputs"].get(key)) for key in changes},
                "source_differences": changed_parameters(source["inputs"], project["inputs"]),
                "target_inputs_sha256": fingerprint(values), "impact": component["impact"],
                "note": "部件参数反馈记录；重新计算后仍需核对工程约束，不代表特性图或硬件验收。"}
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row or row["revision"] != data.revision or project["revision"] != data.revision:
                raise HTTPException(409, "方案版本已更新；部件编辑仍保留，请载入最新版本后反馈。")
            revision = data.revision + (1 if changes else 0)
            encoded = json.dumps(values, ensure_ascii=False, allow_nan=False)
            if changes:
                db.execute("UPDATE projects SET revision=?,inputs=?,updated_by=?,updated=? WHERE id=?",
                           (revision, encoded, user["id"], now, project_id))
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)",
                           (project_id, revision, row["name"], row["description"], encoded, user["id"], now))
            # Signatures are retained; the UI invalidates only signatures whose inputs changed.
            previous = db.execute("SELECT reviews FROM design_group_reviews WHERE project_id=? AND revision=?",
                                  (project_id, data.revision)).fetchone()
            if previous and changes:
                db.execute("INSERT INTO design_group_reviews VALUES(?,?,?,?,?)",
                           (project_id, revision, previous["reviews"], user["id"], now))
            previous_feedback = None
            if not changes:
                for candidate in db.execute(
                    "SELECT id,snapshot FROM component_iterations WHERE project_id=? AND source_job_id=? "
                    "AND component=? AND target_revision=?", (project_id, source["id"], data.component, revision)):
                    saved = json.loads(candidate["snapshot"])
                    if (saved["target_inputs_sha256"] == snapshot["target_inputs_sha256"]
                            and saved["changes"] == snapshot["changes"]):
                        previous_feedback = candidate["id"], saved
                        break
            if previous_feedback:
                key, snapshot = previous_feedback
            else:
                db.execute("INSERT INTO component_iterations VALUES(?,?,?,?,?,?,?,?)",
                           (key, project_id, source["id"], data.component, revision, user["id"], now,
                            json.dumps(snapshot, ensure_ascii=False, allow_nan=False)))
        return {"id": key, "project": get_project(project_id), "warnings": warnings,
                "status": "awaiting_recompute", "snapshot": snapshot}

    @app.post("/api/projects/{project_id}/engineering/baselines")
    async def capture_baseline(project_id: str, request: Request):
        user = session_user(request)
        data = await body(request, CandidateBaseline)
        get_project(project_id)
        source = fixed_source(project_id, data.source_job_id)
        try:
            require_supported_source(source)
            snapshot = baseline_snapshot(source)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            previous = db.execute("SELECT id FROM hardware_baselines WHERE project_id=? AND source_job_id=?",
                                  (project_id, source["id"])).fetchone()
            if previous:
                key = previous["id"]
            else:
                db.execute("INSERT INTO hardware_baselines VALUES(?,?,?,?,?,?)",
                           (key, project_id, source["id"], user["id"], now,
                            json.dumps(snapshot, ensure_ascii=False, allow_nan=False)))
        return baseline_record(project_id, key, request)

    @app.get("/api/projects/{project_id}/engineering/baselines/{baseline_id}")
    def baseline_record(project_id: str, baseline_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        row = store.one("SELECT * FROM hardware_baselines WHERE id=? AND project_id=?", (baseline_id, project_id))
        if not row:
            raise HTTPException(404, "候选硬件基准不存在。")
        row["snapshot"] = json.loads(row["snapshot"])
        row["integrity"] = fingerprint(row["snapshot"]["payload"]) == row["snapshot"]["sha256"]
        return row

    @app.get("/api/projects/{project_id}/engineering/baselines/{baseline_id}/download")
    def download_baseline(project_id: str, baseline_id: str, request: Request):
        row = baseline_record(project_id, baseline_id, request)
        return JSONResponse(row, headers={"Content-Disposition":
                            f'attachment; filename="lre-hardware-candidate-{row["id"]}.json"'})
