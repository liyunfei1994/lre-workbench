"""Task-bound outer-control definitions and isolated algebraic diagnostics."""
import asyncio
import json
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from webapp import control_trim
from webapp.validation import fingerprint


def register_control_trim_routes(app, store, session_user, fixed_record, fixed_source):
    prefix = "/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}/trim"

    def bound_source(record):
        return fixed_source(record["project_id"], record["source_job_id"])

    def definition_record(project_id, candidate_id, trim_id, request):
        fixed_record(project_id, candidate_id, request)
        row = store.one("SELECT * FROM fixed_steady_trim_definitions WHERE id=? AND task_id=?",
                        (trim_id, candidate_id))
        if not row:
            raise HTTPException(404, "当前任务下不存在此配平定义。")
        row["definition"] = json.loads(row["definition"])
        return row

    @app.get(prefix + "/setup")
    def setup(project_id: str, candidate_id: str, request: Request):
        record = fixed_record(project_id, candidate_id, request)
        try:
            return control_trim.configuration(record, bound_source(record))
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(409, str(exc) or "来源不足以建立配平定义。") from exc

    @app.get(prefix + "/definitions")
    def definitions(project_id: str, candidate_id: str, request: Request):
        fixed_record(project_id, candidate_id, request)
        rows = store.all("SELECT * FROM fixed_steady_trim_definitions WHERE task_id=? "
                         "ORDER BY created DESC LIMIT 100", (candidate_id,))
        for row in rows:
            row["definition"] = json.loads(row["definition"])
        return rows

    @app.post(prefix + "/definitions")
    async def save_definition(project_id: str, candidate_id: str, request: Request):
        record = fixed_record(project_id, candidate_id, request)
        user = session_user(request)
        try:
            definition = control_trim.normalize_request(await request.json(), record, bound_source(record))
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise HTTPException(422, str(exc) or "配平任务定义无效。") from exc
        key, now = str(uuid.uuid4()), time.time()
        store.execute("INSERT INTO fixed_steady_trim_definitions(id,task_id,submitted_by,created,definition) "
                      "VALUES(?,?,?,?,?)", (key, candidate_id, user["id"], now,
                                            json.dumps(definition, ensure_ascii=False, allow_nan=False)))
        return {"id": key, "task_id": candidate_id, "submitted_by": user["id"], "created": now,
                "definition": definition}

    @app.get(prefix + "/definitions/download")
    def download_definition(project_id: str, candidate_id: str, trim_id: str, request: Request):
        record = definition_record(project_id, candidate_id, trim_id, request)
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="steady-control-trim-{record["id"]}.json"'})

    @app.post(prefix + "/run")
    async def run(project_id: str, candidate_id: str, trim_id: str, request: Request):
        record = definition_record(project_id, candidate_id, trim_id, request)
        try:
            control_trim.assert_runnable(record["definition"])
        except ValueError as exc:
            message = str(exc)
        else:
            # No executable adapter exists; tampering with readiness or the
            # guard cannot dispatch a concept-design calculation.
            message = "固定硬件稳态内层与实际配平适配器尚未接入。"
        raise HTTPException(409, {"status": "blocked", "can_run": False,
                                  "reason": "inner_model_unavailable", "message": message,
                                  "blockers": record["definition"].get("readiness", {}).get("blockers", [])})

    @app.get(prefix + "/checks")
    def checks(project_id: str, candidate_id: str, request: Request):
        fixed_record(project_id, candidate_id, request)
        rows = store.all("SELECT * FROM fixed_steady_trim_checks WHERE task_id=? "
                         "ORDER BY created DESC LIMIT 100", (candidate_id,))
        for row in rows:
            row["report"] = json.loads(row["report"])
        return rows

    @app.post(prefix + "/checks")
    async def run_check(project_id: str, candidate_id: str, request: Request):
        record = fixed_record(project_id, candidate_id, request)
        user = session_user(request)
        try:
            value = await request.json()
            scenarios = ("reachable", "bound_limited", "inner_failure", "rank_deficient")
            if (not isinstance(value, dict) or set(value) != {"scenario"}
                    or value["scenario"] not in scenarios):
                raise ValueError("双层数值自检只接受指定的 scenario，不接受发动机参数或表达式。")
        except (ValueError, TypeError, RecursionError) as exc:
            raise HTTPException(422, str(exc) or "自检请求无效。") from exc
        from lre_workbench.systems.control_trim import run_trim_framework_check
        report = await asyncio.to_thread(run_trim_framework_check, scenario=value["scenario"])
        if (report.get("analysis_type") != "control_trim_framework_check"
                or report.get("scope") != "numerical_framework_only"
                or report.get("is_engine_result") is not False):
            raise HTTPException(500, "双层数值自检身份不匹配，未保存。")
        report["context"] = {"task_id": candidate_id, "task_sha256": fingerprint(record["task"]),
                             "hardware_id": record["task"]["hardware"]["id"], "engine_inputs_used": False}
        key, now = str(uuid.uuid4()), time.time()
        store.execute("INSERT INTO fixed_steady_trim_checks(id,task_id,submitted_by,created,report) "
                      "VALUES(?,?,?,?,?)", (key, candidate_id, user["id"], now,
                                            json.dumps(report, ensure_ascii=False, allow_nan=False)))
        return {"id": key, "task_id": candidate_id, "submitted_by": user["id"], "created": now,
                "report": report}

    @app.get(prefix + "/checks/download")
    def download_check(project_id: str, candidate_id: str, check_id: str, request: Request):
        fixed_record(project_id, candidate_id, request)
        row = store.one("SELECT * FROM fixed_steady_trim_checks WHERE id=? AND task_id=?", (check_id, candidate_id))
        if not row:
            raise HTTPException(404, "当前任务下不存在此双层数值自检记录。")
        row["report"] = json.loads(row["report"])
        return JSONResponse(row, headers={
            "Content-Disposition": f'attachment; filename="control-trim-framework-check-{row["id"]}.json"'})
