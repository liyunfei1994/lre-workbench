"""Loopback-first authenticated workspace API and local static interface."""
from contextlib import asynccontextmanager
import asyncio
import hashlib
import hmac
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import secrets
import time
import uuid
from urllib.parse import urlsplit
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from webapp.store import Store
from webapp.jobs import JobQueue
from webapp.solver import design_readiness, input_values, presets, reference_engines, SCIENCE_PINS
from webapp.design_scope import DESIGN_SCOPE, require_supported_design, require_supported_source
from webapp.ui_schema import design_ui_schema
from webapp.access import (LOOPBACK, PUBLIC_ENDPOINTS, authorize_resource,
                           https_origin, owned_projects_sql)

ROOT = Path(__file__).resolve().parent.parent
COOKIE = "lre_session"
MAX_BODY = 200_000
PASSWORD_MIN_LENGTH = 1
PASSWORD_MAX_LENGTH = 128


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Model):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    # Existing accounts may predate the creation policy. Verify their password
    # without forcing a length change before they can sign in.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class Registration(Login):
    display_name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class PasswordChange(Model):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class DesignGroupReview(Model):
    kind: Literal["confirmed", "preset"]
    signature: str = Field(min_length=1, max_length=100_000)


DesignGroupId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")]


class DesignReviews(Model):
    revision: int = Field(ge=1, strict=True)
    design_reviews: dict[DesignGroupId, DesignGroupReview] = Field(max_length=100)


class Project(Model):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    inputs: dict
    revision: int | None = Field(default=None, ge=1)
    design_reviews: dict[DesignGroupId, DesignGroupReview] = Field(default_factory=dict, max_length=100)


class DesignReadiness(Model):
    inputs: dict


class Submit(Model):
    revision: int = Field(ge=1)
    mode: Literal["concept_design"] = "concept_design"


class StartupAsset(Model):
    path: str = Field(max_length=100, pattern=r"^/static/[a-z0-9-]+\.js$")
    status: int = Field(ge=0, le=599)
    kind: Literal["ok", "http", "mime", "network"]


class StartupReport(Model):
    kind: Literal["module_load", "syntax"]
    files: list[StartupAsset] = Field(max_length=40)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
    return salt + ":" + value.hex()


def public_user(row):
    return {key: row[key] for key in ("id", "username", "display_name", "role")}


def create_app(database=None, *, queue_enabled=True, job_timeout=600,
               private_workspace=False, public_origin=None, personal_workspace=False):
    public_origin = https_origin(public_origin)
    if personal_workspace and (private_workspace or public_origin):
        raise ValueError("个人工作区不能同时启用成员试用或 HTTPS 校外入口。")
    default_db = ROOT / "user_data/web-workbench/workspace.sqlite3"
    database = Path(database or os.environ.get("LRE_WEB_DB", str(default_db))).resolve()
    if (public_origin or private_workspace) and database == default_db.resolve():
        raise ValueError("校外试用请指定独立数据库，不能使用当前本机研究工作区。")
    store = Store(database)
    personal_owner_id = None
    with store.connect(immediate=True) as db:
        saved_access = db.execute("SELECT value FROM workspace_settings WHERE key='access'").fetchone()
        saved_access = saved_access["value"] if saved_access else "shared"
        if saved_access == "personal" and (private_workspace or public_origin):
            raise ValueError("已保存的个人工作区不能改为成员试用或 HTTPS 校外入口。")
        if saved_access == "private" and personal_workspace:
            raise ValueError("独立试用工作区不能直接改为个人工作区。")
        personal_workspace = personal_workspace or saved_access == "personal"
        if personal_workspace:
            administrators = db.execute("SELECT id FROM users WHERE role='admin'").fetchall()
            has_users = db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
            if len(administrators) > 1 or (has_users and not administrators):
                raise ValueError("个人工作区必须有唯一管理员；请先明确账号归属，不能自动选择所有者。")
            saved_owner = db.execute("SELECT value FROM workspace_settings WHERE key='personal_owner_id'").fetchone()
            personal_owner_id = administrators[0]["id"] if administrators else None
            if saved_owner and saved_owner["value"] != personal_owner_id:
                raise ValueError("个人工作区所有者与已绑定账号不一致，不能自动重新绑定。")
            workspace_access = "personal"
            if personal_owner_id:
                db.execute("INSERT INTO workspace_settings VALUES('personal_owner_id',?) "
                           "ON CONFLICT(key) DO NOTHING", (personal_owner_id,))
                # Keep historical users and their work. Only revoke access.
                db.execute("DELETE FROM sessions WHERE user_id<>?", (personal_owner_id,))
        else:
            if public_origin and not db.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone():
                raise ValueError("请先在本机初始化独立试用工作区的管理员，再启用 HTTPS 入口。")
            workspace_access = "private" if private_workspace or public_origin or saved_access == "private" else "shared"
        if workspace_access != "shared":
            db.execute("INSERT INTO workspace_settings VALUES('access',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (workspace_access,))
    private_workspace = workspace_access == "private"
    queue = JobQueue(store, timeout=job_timeout)

    async def private_access(request: Request):
        if (private_workspace or personal_workspace) and request.url.path.startswith("/api/") and request.url.path not in PUBLIC_ENDPOINTS:
            authorize_resource(request, session_user(request), store)

    async def active_design_access(request: Request):
        # All project mutations, including registered study/trim routes, share
        # this gate. Historical GETs and downloads remain unchanged.
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return
        parts = request.url.path.strip("/").split("/")
        if len(parts) < 3 or parts[:2] != ["api", "projects"]:
            return
        session_user(request)
        project_id = parts[2]
        row = store.one("SELECT inputs FROM projects WHERE id=?", (project_id,))
        if not row:
            return  # The endpoint retains its existing not-found contract.
        try:
            require_supported_design(json.loads(row["inputs"]))
            try:
                payload = await request.json()
            except (ValueError, RecursionError):
                payload = None  # The endpoint reports malformed request bodies.
            source_id = payload.get("source_job_id") if isinstance(payload, dict) else None
            if len(parts) >= 6 and parts[3:5] == ["fixed-steady", "tasks"]:
                candidate = store.one("SELECT source_job_id FROM fixed_steady_tasks WHERE id=? AND project_id=?",
                                      (parts[5], project_id))
                if candidate:
                    source_id = candidate["source_job_id"]
            if isinstance(source_id, str):
                source = store.one("SELECT inputs,result FROM jobs WHERE id=? AND project_id=? AND kind='design'",
                                   (source_id, project_id))
                if source:
                    require_supported_source(source)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @asynccontextmanager
    async def lifespan(app):
        if queue_enabled:
            queue.start()
        try:
            yield
        finally:
            if queue_enabled:
                queue.stop()

    app = FastAPI(title="LRE Workbench", lifespan=lifespan, docs_url=None,
                  redoc_url=None, openapi_url=None, dependencies=[Depends(private_access), Depends(active_design_access)])
    app.state.store, app.state.queue = store, queue
    app.state.private_workspace = private_workspace
    app.state.personal_workspace = personal_workspace
    app.state.workspace_access = workspace_access
    hosts = ["127.0.0.1", "localhost", "[::1]"]
    if public_origin:
        hosts.append(urlsplit(public_origin).hostname)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def boundary(request, call_next):
        if public_origin:
            # The runner binds loopback and does not let Uvicorn rewrite client
            # addresses. Only a local HTTPS reverse proxy may reach this mode.
            if not request.client or request.client.host not in LOOPBACK:
                return JSONResponse({"detail": "校外入口必须经本机 HTTPS 代理访问。"}, status_code=403)
            local_health = (request.method == "GET" and request.url.path == "/api/health"
                            and request.headers.get("host", "").split(":")[0] == "127.0.0.1")
            if not local_health and (request.headers.get("host", "").lower() != urlsplit(public_origin).netloc
                                     or request.headers.get("x-forwarded-proto") != "https"):
                return JSONResponse({"detail": "请使用已配置的 HTTPS 校外入口。"}, status_code=403)
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            # Browser writes must be same-origin JSON; never accept a cross-site
            # form, a missing Origin, or an unbounded streaming request body.
            origin = request.headers.get("origin")
            if origin != (public_origin or str(request.base_url).rstrip("/")):
                return JSONResponse({"detail": "请从工作台页面发起操作。"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return JSONResponse({"detail": "请求必须为 JSON。"}, status_code=415)
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BODY:
                    return JSONResponse({"detail": "请求内容超过大小限制。"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        return response

    async def body(request, model):
        try:
            return model.model_validate(await request.json())
        except ValidationError as exc:
            password_field = {Registration: "password", PasswordChange: "new_password"}.get(model)
            for error in exc.errors(include_input=False, include_context=False, include_url=False):
                if password_field and error["loc"] == (password_field,):
                    label = "新密码" if model is PasswordChange else "密码"
                    if error["type"] == "string_too_short":
                        raise HTTPException(422, f"{label}不能为空。") from None
                    if error["type"] == "string_too_long":
                        raise HTTPException(422, f"{label}最多为 {PASSWORD_MAX_LENGTH} 个字符。") from None
            raise HTTPException(422, "输入格式不正确，请检查字段、长度和数值。") from None
        except ValueError:
            raise HTTPException(422, "输入格式不正确，请检查字段、长度和数值。")

    def session_user(request, required=True):
        token = request.cookies.get(COOKIE, "")
        row = store.one(
            "SELECT u.* FROM sessions s JOIN users u ON s.user_id=u.id "
            "WHERE s.token_hash=? AND s.expires>?", (digest(token), time.time())) if token else None
        if personal_workspace and row and row["id"] != personal_owner_id:
            row = None
        if required and not row:
            raise HTTPException(401, "请先登录。")
        return row

    def administrator(request):
        row = session_user(request)
        if row["role"] != "admin":
            raise HTTPException(403, "此操作需要管理员权限。")
        return row

    def set_session(row):
        token = secrets.token_urlsafe(40)
        now = time.time()
        with store.connect(immediate=True) as db:
            if personal_workspace and row["id"] != personal_owner_id:
                raise HTTPException(401, "此个人工作区仅允许所有者登录。")
            # Password verification runs outside this transaction. A concurrent
            # password change must not let an old-password login create a new
            # session after all existing sessions have been revoked.
            current = db.execute("SELECT password_hash FROM users WHERE id=?", (row["id"],)).fetchone()
            if not current or not hmac.compare_digest(current["password_hash"], row["password_hash"]):
                raise HTTPException(401, "账号凭据已更新，请使用新密码重新登录。")
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), row["id"], now + 12*3600))
        response = JSONResponse(public_user(row))
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            secure=bool(public_origin), max_age=12*3600, path="/")
        return response

    def get_design_reviews(project_id, revision):
        row = store.one("SELECT reviews FROM design_group_reviews WHERE project_id=? AND revision=?",
                        (project_id, revision))
        return json.loads(row["reviews"]) if row else {}

    def write_design_reviews(db, project_id, revision, reviews, user_id, now):
        # Review metadata belongs to an input revision, never to EngineInputs.
        # Limit the merged document as well as each HTTP request.
        encoded = json.dumps(reviews, ensure_ascii=False, allow_nan=False)
        if len(reviews) > 100 or len(encoded.encode("utf-8")) > MAX_BODY:
            raise HTTPException(422, "参数确认记录超过大小限制。")
        db.execute("INSERT INTO design_group_reviews VALUES(?,?,?,?,?) "
                   "ON CONFLICT(project_id,revision) DO UPDATE SET reviews=excluded.reviews,"
                   "updated_by=excluded.updated_by,updated=excluded.updated",
                   (project_id, revision, encoded, user_id, now))

    def get_project(project_id):
        row = store.one(
            "SELECT p.*,u.display_name AS author FROM projects p "
            "JOIN users u ON u.id=p.updated_by WHERE p.id=?", (project_id,))
        if not row:
            raise HTTPException(404, "项目不存在。")
        row["inputs"] = json.loads(row["inputs"])
        row["design_reviews"] = get_design_reviews(project_id, row["revision"])
        return row

    def normalize_project(data):
        try:
            require_supported_design(data.inputs)
            values, messages = input_values(data.inputs)
        except Exception as exc:
            raise HTTPException(422, str(exc)[:1600])
        name = data.name.strip()
        if not name:
            raise HTTPException(422, "请输入项目名称。")
        return name, json.dumps(values, ensure_ascii=False, allow_nan=False), messages

    @app.get("/api/health")
    def health():
        return {"service": "lre-workbench-web", "status": "ok", "edition": "local-preview",
                "workspace_id": digest(str(store.path)), "public_origin": public_origin,
                "workspace_access": workspace_access}

    startup_reports = []

    @app.post("/api/startup-report")
    async def startup_report(request: Request):
        # A visitor can hit a startup failure before reaching app login. The
        # exact HTTPS proxy and same-origin JSON checks still apply. Never
        # accept arbitrary logs, URLs, headers or identities in this endpoint.
        if not public_origin:
            raise HTTPException(404, "此入口不可用。")
        report = await body(request, StartupReport)
        now = time.monotonic()
        startup_reports[:] = [stamp for stamp in startup_reports if now-stamp < 60]
        if len(startup_reports) >= 6:
            raise HTTPException(429, "诊断提交过于频繁，请稍后再试。")
        startup_reports.append(now)
        report_id = secrets.token_hex(6)
        logging.getLogger("uvicorn.error").warning("STARTUP_DIAGNOSTIC %s %s", report_id,
                                                 json.dumps(report.model_dump(), ensure_ascii=True))
        return {"id": report_id}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        row = session_user(request, required=False)
        return {"initialized": bool(store.one("SELECT id FROM users LIMIT 1")),
                "user": public_user(row) if row else None,
                "remote": bool(public_origin), "workspace_access": workspace_access}

    @app.post("/api/auth/setup")
    async def setup(request: Request):
        nonlocal personal_owner_id
        if public_origin or request.client.host not in LOOPBACK:
            raise HTTPException(403, "首次创建管理员只能在本机完成。")
        data = await body(request, Registration)
        encoded = await asyncio.to_thread(password_hash, data.password)
        row = {"id": str(uuid.uuid4()), "username": data.username.lower(),
               "display_name": data.display_name.strip(), "password_hash": encoded,
               "role": "admin", "created": time.time()}
        if not row["display_name"]:
            raise HTTPException(422, "请输入显示名称。")
        with store.connect(immediate=True) as db:
            if db.execute("SELECT id FROM users LIMIT 1").fetchone():
                raise HTTPException(409, "工作区已经初始化，请登录。")
            db.execute("INSERT INTO users VALUES(:id,:username,:display_name,:password_hash,:role,:created)", row)
            if personal_workspace:
                db.execute("INSERT INTO workspace_settings VALUES('personal_owner_id',?)", (row["id"],))
        if personal_workspace:
            personal_owner_id = row["id"]
        return set_session(row)

    @app.post("/api/auth/login")
    async def login(request: Request):
        data = await body(request, Login)
        # A shared per-client budget also bounds attempts against unknown users.
        # A tunnel presents one loopback peer for all visitors. Do not trust
        # caller-supplied IP headers; bound both account and aggregate work.
        key = digest("account:" + data.username.lower()) if public_origin else digest(request.client.host)
        budgets = [(key, 10)]
        if public_origin:
            budgets.append((digest("remote-login-budget"), 120))
        now = time.time()
        with store.connect(immediate=True) as db:
            db.execute("DELETE FROM login_limits WHERE until_time<?", (now,))
            for budget_key, maximum in budgets:
                limit = db.execute("SELECT attempts FROM login_limits WHERE key=?", (budget_key,)).fetchone()
                if limit and limit["attempts"] >= maximum:
                    raise HTTPException(429, "登录尝试过多，请 15 分钟后再试。")
                db.execute("INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(key) "
                           "DO UPDATE SET attempts=attempts+1", (budget_key, now+900))
        row = store.one("SELECT * FROM users WHERE username=?", (data.username.lower(),))
        if personal_workspace and row and row["id"] != personal_owner_id:
            row = None
        encoded = await asyncio.to_thread(password_hash, data.password,
                                          row["password_hash"].split(":")[0] if row else "00"*16)
        if not row or not hmac.compare_digest(row["password_hash"], encoded):
            raise HTTPException(401, "用户名或密码不正确。")
        store.execute("DELETE FROM login_limits WHERE key=?", (key,))
        return set_session(row)

    @app.post("/api/auth/change-password")
    async def change_password(request: Request):
        row = session_user(request)
        data = await body(request, PasswordChange)
        budget_key = digest("password-change:" + row["id"])
        now = time.time()
        # Reserve before hashing so parallel wrong-current-password attempts
        # cannot bypass the per-account verification budget.
        with store.connect(immediate=True) as db:
            db.execute("DELETE FROM login_limits WHERE until_time<?", (now,))
            limit = db.execute("SELECT attempts FROM login_limits WHERE key=?", (budget_key,)).fetchone()
            if limit and limit["attempts"] >= 10:
                raise HTTPException(429, "密码验证尝试过多，请 15 分钟后再试。")
            db.execute("INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(key) "
                       "DO UPDATE SET attempts=attempts+1", (budget_key, now+900))
        verified = await asyncio.to_thread(password_hash, data.current_password,
                                           row["password_hash"].split(":")[0])
        if not hmac.compare_digest(row["password_hash"], verified):
            raise HTTPException(400, "当前密码不正确。")
        if hmac.compare_digest(data.current_password.encode(), data.new_password.encode()):
            raise HTTPException(400, "新密码不能与当前密码相同。")
        encoded = await asyncio.to_thread(password_hash, data.new_password)
        now = time.time()
        with store.connect(immediate=True) as db:
            active = db.execute(
                "SELECT u.password_hash FROM sessions s JOIN users u ON s.user_id=u.id "
                "WHERE s.token_hash=? AND s.user_id=? AND s.expires>?",
                (digest(request.cookies.get(COOKIE, "")), row["id"], now)).fetchone()
            if not active or not hmac.compare_digest(active["password_hash"], row["password_hash"]):
                raise HTTPException(401, "登录状态或账号凭据已更新，请重新登录。")
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (encoded, row["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            db.execute("INSERT INTO account_security VALUES(?,?) ON CONFLICT(user_id) "
                       "DO UPDATE SET password_changed_at=excluded.password_changed_at", (row["id"], now))
            db.execute("DELETE FROM login_limits WHERE key=?", (budget_key,))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=bool(public_origin),
                               httponly=True, samesite="strict")
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        store.execute("DELETE FROM sessions WHERE token_hash=?", (digest(request.cookies.get(COOKIE, "")),))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/members")
    def members(request: Request):
        administrator(request)
        if personal_workspace:
            raise HTTPException(403, "个人工作区不提供成员管理。")
        return store.all("SELECT id,username,display_name,role,created FROM users ORDER BY created")

    @app.post("/api/members")
    async def create_member(request: Request):
        administrator(request)
        if personal_workspace:
            raise HTTPException(403, "个人工作区不提供成员管理。")
        data = await body(request, Registration)
        encoded = await asyncio.to_thread(password_hash, data.password)
        row = {"id": str(uuid.uuid4()), "username": data.username.lower(),
               "display_name": data.display_name.strip(), "password_hash": encoded,
               "role": "member", "created": time.time()}
        if not row["display_name"]:
            raise HTTPException(422, "请输入显示名称。")
        with store.connect(immediate=True) as db:
            if db.execute("SELECT id FROM users WHERE username=?", (row["username"],)).fetchone():
                raise HTTPException(409, "该用户名已存在。")
            db.execute("INSERT INTO users VALUES(:id,:username,:display_name,:password_hash,:role,:created)", row)
        return public_user(row)

    @app.get("/api/info")
    def info(request: Request):
        session_user(request)
        return {"python": os.sys.version.split()[0], "scope": "external-pilot" if public_origin else "local",
                "workspace_access": workspace_access,
                "max_parallel_jobs": 1, "job_timeout_seconds": job_timeout,
                "design_scope": dict(DESIGN_SCOPE),
                "design_readiness": True,
                "design_review_persistence": True,
                "presets": presets(), "reference_engines": reference_engines(),
                "ui_schema": design_ui_schema(),
                "dependencies": {k: importlib.metadata.version(k) for k in SCIENCE_PINS}}

    @app.get("/api/projects")
    def projects(request: Request):
        user = session_user(request)
        restricted = private_workspace and user["role"] != "admin"
        return store.all(
            "SELECT p.id,p.name,p.description,p.revision,p.created,p.updated,u.display_name AS author,"
            "(SELECT COUNT(*) FROM jobs j WHERE j.project_id=p.id AND j.kind='design') AS job_count "
            "FROM projects p JOIN users u ON u.id=p.updated_by "
            + ("WHERE " + owned_projects_sql() + " " if restricted else "")
            + "ORDER BY p.updated DESC", (user["id"],) if restricted else ())

    @app.post("/api/projects")
    async def create_project(request: Request):
        user = session_user(request)
        data = await body(request, Project)
        name, values, messages = normalize_project(data)
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?)",
                       (key, name, data.description, 1, values, user["id"], now, now))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)",
                       (key, 1, name, data.description, values, user["id"], now))
            write_design_reviews(db, key, 1, data.model_dump()["design_reviews"], user["id"], now)
        return {"project": get_project(key), "warnings": messages}

    @app.get("/api/projects/{project_id}")
    def project(project_id: str, request: Request):
        session_user(request)
        return get_project(project_id)

    @app.post("/api/projects/{project_id}/design-readiness")
    async def check_design_readiness(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        data = await body(request, DesignReadiness)
        return design_readiness(data.inputs)

    @app.put("/api/projects/{project_id}/design-reviews")
    async def confirm_design_groups(project_id: str, request: Request):
        user = session_user(request)
        data = await body(request, DesignReviews)
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT revision FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise HTTPException(404, "项目不存在。")
            if row["revision"] != data.revision:
                raise HTTPException(409, "此方案已更新，请载入最新版本后确认参数。")
            saved = db.execute("SELECT reviews FROM design_group_reviews WHERE project_id=? AND revision=?",
                               (project_id, data.revision)).fetchone()
            reviews = json.loads(saved["reviews"]) if saved else {}
            reviews.update(data.model_dump()["design_reviews"])
            write_design_reviews(db, project_id, data.revision, reviews, user["id"], time.time())
        return {"project": get_project(project_id)}

    @app.put("/api/projects/{project_id}")
    async def save(project_id: str, request: Request):
        user = session_user(request)
        data = await body(request, Project)
        if data.revision is None:
            raise HTTPException(422, "保存方案需要原版本号。")
        name, values, messages = normalize_project(data)
        now = time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT revision FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise HTTPException(404, "项目不存在。")
            if row["revision"] != data.revision:
                raise HTTPException(409, "此方案已被其他成员更新。你的编辑仍保留，请先查看最新版本再合并。")
            revision = data.revision + 1
            db.execute("UPDATE projects SET name=?,description=?,revision=?,inputs=?,updated_by=?,updated=? WHERE id=?",
                       (name, data.description, revision, values, user["id"], now, project_id))
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)",
                       (project_id, revision, name, data.description, values, user["id"], now))
            write_design_reviews(db, project_id, revision, data.model_dump()["design_reviews"], user["id"], now)
        return {"project": get_project(project_id), "warnings": messages}

    @app.get("/api/projects/{project_id}/revisions")
    def revisions(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        return store.all(
            "SELECT r.revision,r.name,r.created,u.display_name AS author FROM revisions r "
            "JOIN users u ON u.id=r.author WHERE project_id=? ORDER BY revision DESC", (project_id,))

    @app.get("/api/projects/{project_id}/revisions/{revision}")
    def old_revision(project_id: str, revision: int, request: Request):
        session_user(request)
        row = store.one("SELECT * FROM revisions WHERE project_id=? AND revision=?", (project_id, revision))
        if not row:
            raise HTTPException(404, "版本不存在。")
        row["inputs"] = json.loads(row["inputs"])
        row["design_reviews"] = get_design_reviews(project_id, revision)
        return row

    @app.post("/api/projects/{project_id}/jobs")
    async def submit(project_id: str, request: Request):
        user = session_user(request)
        data = await body(request, Submit)
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise HTTPException(404, "项目不存在。")
            if row["revision"] != data.revision:
                raise HTTPException(409, "方案已更新，请刷新并确认后再提交。")
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')",
                             (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务。")
            from lre_workbench.domain.analysis_contract import concept_design_task
            values, _ = input_values(json.loads(row["inputs"]), saved_snapshot=True)
            task = concept_design_task(values)
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,analysis_task) "
                       "VALUES(?,?,?,?,?,?,?,?)",
                       (key, project_id, data.revision, user["id"], "queued",
                        json.dumps(values, ensure_ascii=False, allow_nan=False), now,
                        json.dumps(task, ensure_ascii=False, allow_nan=False)))
        return {"id": key, "status": "queued", "mode": "concept_design"}

    @app.get("/api/jobs")
    def jobs(request: Request, project_id: str | None = None):
        user = session_user(request)
        sql = ("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
               "j.progress,j.error,p.name AS project_name,u.display_name AS author FROM jobs j "
               "JOIN projects p ON p.id=j.project_id JOIN users u ON u.id=j.submitted_by ")
        filters, args = ["j.kind='design'"], []
        if project_id:
            filters.append("j.project_id=?")
            args.append(project_id)
        if private_workspace and user["role"] != "admin":
            filters.append(owned_projects_sql())
            args.append(user["id"])
        rows = store.all(sql + "WHERE " + " AND ".join(filters) + " ORDER BY j.created DESC LIMIT 100", args)
        for row in rows:
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j "
                        "JOIN users u ON u.id=j.submitted_by WHERE j.id=? AND j.kind='design'", (job_id,))
        if not row:
            raise HTTPException(404, "任务不存在。")
        for field in ("inputs", "result", "progress", "analysis_task"):
            row[field] = json.loads(row[field]) if row[field] else None
        row["mode"] = "concept_design"
        try:
            require_supported_source(row)
        except ValueError as exc:
            # Archived foreign-cycle records remain downloadable verbatim;
            # do not attach newly derived FFSC engineering views to them.
            row["scope_issue"] = str(exc)
            return row
        from webapp.nozzle_geometry import nozzle_geometry_view
        row["nozzle_geometry"] = nozzle_geometry_view(row["result"], row["id"])
        from webapp.nozzle_design import nozzle_design_view
        row["nozzle_design"] = nozzle_design_view(row["result"], row["id"])
        from webapp.chamber_design import chamber_design_view
        row["chamber_design"] = chamber_design_view(row["result"], row["id"])
        from webapp.pump_geometry import pump_geometry_view
        row["pump_geometry"] = pump_geometry_view(row["result"], row["id"])
        from webapp.injector_design import injector_design_view
        row["injector_design"] = injector_design_view(row["result"], row["id"])
        from webapp.turbine_geometry import turbine_geometry_view
        row["turbine_geometry"] = turbine_geometry_view(row["result"], row["id"])
        from webapp.turbine_design import turbine_design_view
        row["turbine_design"] = turbine_design_view(row["result"], row["id"])
        from webapp.turbine_workspace import turbine_workspace_view
        row["turbine_design"]["workspace"] = turbine_workspace_view(row["result"], row["turbine_design"])
        from webapp.result_consistency import result_consistency_view
        row["result_consistency"] = result_consistency_view(row["result"], row["id"])
        from webapp.steady_design import steady_design_view
        row["steady_design"] = steady_design_view(row["result"], row["id"], consistency=row["result_consistency"])
        from webapp.valve_requirements import valve_requirements_view
        row["valve_requirements"] = valve_requirements_view(row["result"], row["id"])
        return row

    @app.post("/api/jobs/{job_id}/nozzle-ambient")
    async def nozzle_ambient(job_id: str, request: Request):
        record = job(job_id, request)
        if record.get("status") != "succeeded" or not record.get("result"):
            raise HTTPException(409, "请选用已完成的循环设计记录。")
        if record.get("scope_issue"):
            raise HTTPException(422, record["scope_issue"])
        try:
            from webapp.nozzle_design import nozzle_ambient_view
            return nozzle_ambient_view(record["result"], await request.json(), record["id"])
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise HTTPException(422, str(exc)[:1200] or "环境特性参数无效。") from exc

    @app.post("/api/jobs/{job_id}/turbine-preliminary")
    async def turbine_preliminary(job_id: str, request: Request):
        # Compute a bounded, independent scenario; this endpoint never persists
        # the scenario, edits a cycle snapshot, or queues a new engine solve.
        record = job(job_id, request)
        if record.get("status") != "succeeded" or not record.get("result"):
            raise HTTPException(409, "请选用已完成的循环设计记录。")
        if record.get("scope_issue"):
            raise HTTPException(422, record["scope_issue"])
        try:
            value = await request.json()
            if not isinstance(value, dict) or set(value) != {"turbine_key", "scenario"}:
                raise ValueError("初选请求须包含涡轮侧别和方案参数。")
            if not isinstance(value["turbine_key"], str) or not isinstance(value["scenario"], dict):
                raise ValueError("涡轮侧别或方案参数格式无效。")
            from webapp.turbine_workspace import preliminary_result
            return preliminary_result(record["result"], record["turbine_design"], value["turbine_key"], value["scenario"])
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise HTTPException(422, str(exc)[:1200] or "初选参数无效。") from exc

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        user = session_user(request)
        row = store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise HTTPException(404, "任务不存在。")
        if user["role"] != "admin" and row["submitted_by"] != user["id"]:
            raise HTTPException(403, "只能取消自己提交的任务。")
        changed = store.execute("UPDATE jobs SET status='cancelled',finished=? "
                                "WHERE id=? AND status IN ('queued','running')", (time.time(), job_id))
        if not changed:
            raise HTTPException(409, "任务已经结束。")
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/download")
    def download_job(job_id: str, request: Request):
        record = job(job_id, request)
        if record["result"] is None:
            raise HTTPException(409, "此任务尚无可导出的结果。")
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="lre-result-{record["id"]}.json"'})

    @app.get("/api/validation/capabilities")
    def validation_capabilities(request: Request):
        session_user(request)
        from webapp.validation import capabilities
        return capabilities()

    @app.get("/api/projects/{project_id}/validations")
    def validations(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.finished,"
                         "j.progress,j.error,j.inputs,u.display_name AS author FROM jobs j "
                         "JOIN users u ON u.id=j.submitted_by WHERE j.project_id=? AND j.kind='validation' "
                         "ORDER BY j.created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/validations")
    async def submit_validation(project_id: str, request: Request):
        user = session_user(request)
        try:
            value = await request.json()
            if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
                raise ValueError("请选择已完成的来源计算记录。")
            try:
                uuid.UUID(value["source_job_id"])
            except (ValueError, AttributeError):
                raise ValueError("来源计算记录标识无效。") from None
        except (ValueError, RecursionError) as exc:
            raise HTTPException(422, "校核 JSON 或来源记录标识无效。") from exc
        key, now = str(uuid.uuid4()), time.time()
        from webapp.validation import normalize_request
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                             "AND status='succeeded' AND result IS NOT NULL",
                             (value["source_job_id"], project_id)).fetchone()
            if not row:
                raise HTTPException(422, "来源记录必须是本项目已完成的系统设计计算。")
            source = dict(row)
            source["inputs"] = json.loads(source["inputs"])
            try:
                normalized = normalize_request(value, source)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                detail = str(exc)[:1600].encode("utf-8", "backslashreplace").decode("utf-8")
                raise HTTPException(422, detail)
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')",
                             (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务。")
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                       "VALUES(?,?,?,?,?,?,?,'validation')", (key, project_id, source["revision"], user["id"],
                        "queued", json.dumps(normalized, ensure_ascii=False, allow_nan=False), now))
        return {"id": key, "status": "queued"}

    @app.get("/api/validations/{validation_id}")
    def validation_record(validation_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j JOIN users u ON u.id=j.submitted_by "
                        "WHERE j.id=? AND j.kind='validation'", (validation_id,))
        if not row:
            raise HTTPException(404, "校核记录不存在。")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/validations/{validation_id}/cancel")
    async def cancel_validation(validation_id: str, request: Request):
        validation_record(validation_id, request)
        return await cancel(validation_id, request)

    def fixed_source(project_id, source_job_id):
        source = store.one("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' AND status='succeeded'",
                           (source_job_id, project_id))
        if not source:
            raise HTTPException(422, "来源必须为本项目已完成的概念设计记录。")
        source["inputs"] = json.loads(source["inputs"])
        source["result"] = json.loads(source["result"])
        return source

    @app.get("/api/projects/{project_id}/fixed-steady/setup")
    def fixed_steady_setup(project_id: str, source_job_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        from webapp.fixed_steady import configuration
        try:
            return configuration(fixed_source(project_id, source_job_id))
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            raise HTTPException(422, str(exc) or "来源记录缺少可冻结的硬件数据。") from exc

    @app.post("/api/projects/{project_id}/fixed-steady/tasks")
    async def save_fixed_steady_task(project_id: str, request: Request):
        user = session_user(request)
        get_project(project_id)
        from webapp.fixed_steady import normalize_request
        try:
            value = await request.json()
            if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
                raise ValueError("请选择本项目已完成的概念设计记录。")
            task = normalize_request(value, fixed_source(project_id, value["source_job_id"]))
        except (ValueError, TypeError, KeyError, StopIteration, RecursionError) as exc:
            raise HTTPException(422, str(exc) or "固定硬件稳态任务定义无效。") from exc
        key, now = str(uuid.uuid4()), time.time()
        store.execute("INSERT INTO fixed_steady_tasks(id,project_id,source_job_id,submitted_by,created,task) "
                      "VALUES(?,?,?,?,?,?)",
                      (key, project_id, value["source_job_id"], user["id"], now,
                       json.dumps(task, ensure_ascii=False, allow_nan=False)))
        return fixed_steady_record(project_id, key, request)

    @app.get("/api/projects/{project_id}/fixed-steady/tasks")
    def fixed_steady_tasks(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT * FROM fixed_steady_tasks WHERE project_id=? ORDER BY created DESC LIMIT 100",
                         (project_id,))
        for row in rows:
            row["task"] = json.loads(row["task"])
        return rows

    @app.get("/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}")
    def fixed_steady_record(project_id: str, candidate_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT * FROM fixed_steady_tasks WHERE id=? AND project_id=?", (candidate_id, project_id))
        if not row:
            raise HTTPException(404, "固定硬件稳态任务不存在。")
        row["task"] = json.loads(row["task"])
        return row

    @app.get("/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}/download")
    def download_fixed_steady_task(project_id: str, candidate_id: str, request: Request):
        record = fixed_steady_record(project_id, candidate_id, request)
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="fixed-steady-task-{record["id"]}.json"'})

    @app.get("/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}/data-template")
    def fixed_data_template(project_id: str, candidate_id: str, request: Request):
        record = fixed_steady_record(project_id, candidate_id, request)
        from webapp.steady_model_contract import data_template
        try:
            package = data_template(record)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse(package, headers={
            "Content-Disposition": f'attachment; filename="fixed-steady-data-template-{record["id"]}.json"'})

    @app.post("/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}/data-check")
    async def check_fixed_data(project_id: str, candidate_id: str, request: Request):
        record = fixed_steady_record(project_id, candidate_id, request)
        from webapp.steady_model_contract import validate_data_package
        try:
            return validate_data_package(await request.json(), record)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise HTTPException(422, str(exc) or "数据登记包无效。") from exc

    @app.get("/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}/framework-checks")
    def fixed_framework_checks(project_id: str, candidate_id: str, request: Request):
        fixed_steady_record(project_id, candidate_id, request)
        rows = store.all("SELECT * FROM fixed_steady_framework_checks WHERE task_id=? "
                         "ORDER BY created DESC LIMIT 100", (candidate_id,))
        for row in rows:
            row["report"] = json.loads(row["report"])
        return rows

    @app.post("/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}/framework-checks")
    async def run_fixed_framework_check(project_id: str, candidate_id: str, request: Request):
        record = fixed_steady_record(project_id, candidate_id, request)
        user = session_user(request)
        try:
            value = await request.json()
            if value != {}:
                raise ValueError("数值框架自检不接受发动机参数、公式或求解设置。")
        except (ValueError, TypeError, RecursionError) as exc:
            raise HTTPException(422, str(exc) or "自检请求须为空对象。") from exc
        from lre_workbench.solvers.iteration import run_framework_check
        from webapp.validation import fingerprint
        # A bounded server-owned algebraic check, outside all engine queues.
        # The link below is provenance only; no engine input is passed to it.
        report = await asyncio.to_thread(run_framework_check)
        if (report.get("analysis_type") != "numerical_framework_check"
                or report.get("scope") != "numerical_framework_only"
                or report.get("is_engine_result") is not False):
            raise HTTPException(500, "数值自检结果身份不匹配，未保存。")
        report["context"] = {"task_id": candidate_id, "task_sha256": fingerprint(record["task"]),
                             "hardware_id": record["task"]["hardware"]["id"],
                             "engine_inputs_used": False}
        key, now = str(uuid.uuid4()), time.time()
        store.execute("INSERT INTO fixed_steady_framework_checks(id,task_id,submitted_by,created,report) "
                      "VALUES(?,?,?,?,?)", (key, candidate_id, user["id"], now,
                                            json.dumps(report, ensure_ascii=False, allow_nan=False)))
        return {"id": key, "task_id": candidate_id, "submitted_by": user["id"], "created": now,
                "report": report}

    @app.get("/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}/framework-checks/download")
    def download_fixed_framework_check(project_id: str, candidate_id: str, check_id: str, request: Request):
        fixed_steady_record(project_id, candidate_id, request)
        row = store.one("SELECT * FROM fixed_steady_framework_checks WHERE id=? AND task_id=?",
                        (check_id, candidate_id))
        if not row:
            raise HTTPException(404, "该任务中不存在此数值自检记录。")
        row["report"] = json.loads(row["report"])
        return JSONResponse(row, headers={
            "Content-Disposition": f'attachment; filename="numerical-framework-check-{row["id"]}.json"'})

    @app.post("/api/projects/{project_id}/fixed-steady/tasks/{candidate_id}/run")
    async def run_fixed_steady_task(project_id: str, candidate_id: str, request: Request):
        record = fixed_steady_record(project_id, candidate_id, request)
        from webapp.fixed_steady import assert_runnable
        try:
            assert_runnable(record["task"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        # A future solver must explicitly implement dispatch here. Never fall
        # through to the concept-design queue, even if readiness is tampered.
        raise HTTPException(409, "固定硬件稳态求解器尚未接入；任务定义已保存。")

    from webapp.control_trim_routes import register_control_trim_routes
    register_control_trim_routes(app, store, session_user, fixed_steady_record, fixed_source)

    @app.get("/api/projects/{project_id}/cooling-designs/setup")
    def cooling_design_setup(project_id: str, source_job_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        source = store.one("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' AND status='succeeded'",
                           (source_job_id, project_id))
        if not source:
            raise HTTPException(422, "请选择本项目已完成的设计点")
        source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
        from webapp.cooling_design import configuration
        try:
            return configuration(source)
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            raise HTTPException(422, str(exc) or "来源记录缺少冷却边界") from exc

    @app.get("/api/projects/{project_id}/cooling-designs")
    def cooling_designs(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT id,project_id,revision,submitted_by,status,created,progress,error,inputs,kind "
                         "FROM jobs WHERE project_id=? AND kind='cooling_design' ORDER BY created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/cooling-designs")
    async def submit_cooling_design(project_id: str, request: Request):
        user = session_user(request)
        try:
            value = await request.json()
        except (ValueError, TypeError, RecursionError) as exc:
            raise HTTPException(422, "请求须为有效 JSON") from exc
        if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
            raise HTTPException(422, "请选择来源设计记录")
        from webapp.cooling_design import normalize_request
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' AND status='succeeded'",
                             (value["source_job_id"], project_id)).fetchone()
            if not row:
                raise HTTPException(422, "来源必须为本项目已完成的稳态设计记录")
            source = dict(row)
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
            try:
                normalized = normalize_request(value, source)
            except (ValueError, TypeError, KeyError, StopIteration) as exc:
                raise HTTPException(422, str(exc) or "来源边界不完整") from exc
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')", (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务")
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) VALUES(?,?,?,?,?,?,?,'cooling_design')",
                       (key, project_id, source["revision"], user["id"], "queued", json.dumps(normalized, ensure_ascii=False, allow_nan=False), now))
        return {"id": key, "status": "queued"}

    @app.get("/api/cooling-designs/{study_id}")
    def cooling_design_record(study_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT * FROM jobs WHERE id=? AND kind='cooling_design'", (study_id,))
        if not row:
            raise HTTPException(404, "通道筛选记录不存在")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/cooling-designs/{study_id}/cancel")
    async def cancel_cooling_design(study_id: str, request: Request):
        cooling_design_record(study_id, request)
        return await cancel(study_id, request)

    @app.get("/api/cooling-designs/{study_id}/candidates/{candidate_id}/draft")
    def cooling_candidate_draft(study_id: str, candidate_id: int, request: Request):
        record = cooling_design_record(study_id, request)
        rows = (record["result"] or {}).get("candidates", [])
        row = next((row for row in rows if row["id"] == candidate_id and row["status"] == "computed"), None)
        if row is None:
            raise HTTPException(422, "该候选尚无计算结果")
        g, limits = row["geometry"], record["request"]["constraints"]
        patch = {"cooling_n_channels": float(g["count"]), "cooling_channel_width_m": g["width_mm"]/1000,
                 "cooling_channel_height_m": g["height_mm"]/1000, "cooling_wall_thickness_m": g["wall_mm"]/1000,
                 "cooling_channel_auto_size": 0.0, "cooling_sizing_reference": "coolant_inlet_velocity",
                 "cooling_wall_temp_limit_status": limits["wall_limit_status"],
                 "cooling_wall_temp_limit_basis": limits["wall_limit_basis"]}
        if limits["wall_temp_limit_K"] is not None:
            patch["cooling_wall_temp_limit_K"] = limits["wall_temp_limit_K"]
        return {"project_id": record["project_id"], "source_job_id": record["request"]["source_job_id"],
                "input_patch": patch, "assessment": row["assessment"],
                "note": "仅采纳几何及本次壁温约束到草稿；先保存新版本，再重新整机闭合，筛选结果不替代设计点结果。"}

    @app.get("/api/jobs/{job_id}/operating-throttle-config")
    def operating_throttle_configuration(job_id: str, request: Request):
        session_user(request)
        source = store.one("SELECT * FROM jobs WHERE id=? AND kind='design' AND status='succeeded' "
                           "AND result IS NOT NULL", (job_id,))
        if not source:
            raise HTTPException(422, "请选择已完成的来源设计记录。")
        source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
        from webapp.operating_throttle import configuration
        return configuration(source)

    @app.get("/api/projects/{project_id}/operating-throttle")
    def operating_throttles(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
                         "j.progress,j.error,j.inputs,u.display_name AS author FROM jobs j "
                         "JOIN users u ON u.id=j.submitted_by WHERE j.project_id=? AND j.kind='operating_throttle' "
                         "ORDER BY j.created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["kind"] = "operating_throttle"
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/operating-throttle")
    async def submit_operating_throttle(project_id: str, request: Request):
        user = session_user(request)
        try:
            value = await request.json()
            if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
                raise ValueError("请选择已完成的来源计算记录。")
            uuid.UUID(value["source_job_id"])
        except (ValueError, AttributeError, RecursionError) as exc:
            raise HTTPException(422, "节流请求 JSON 或来源记录标识无效。") from exc
        from webapp.operating_throttle import normalize_request
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                             "AND status='succeeded' AND result IS NOT NULL",
                             (value["source_job_id"], project_id)).fetchone()
            if not row:
                raise HTTPException(422, "来源记录必须是本项目已完成的设计计算。")
            source = dict(row)
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
            try:
                normalized = normalize_request(value, source)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise HTTPException(422, str(exc)[:1600] or "固定几何节流请求无效。") from exc
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')",
                             (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务。")
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                       "VALUES(?,?,?,?,?,?,?,'operating_throttle')", (key, project_id, source["revision"], user["id"],
                        "queued", json.dumps(normalized, ensure_ascii=False, allow_nan=False), now))
        return {"id": key, "status": "queued"}

    @app.get("/api/operating-throttle/{throttle_id}")
    def operating_throttle_record(throttle_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j JOIN users u ON u.id=j.submitted_by "
                        "WHERE j.id=? AND j.kind='operating_throttle'", (throttle_id,))
        if not row:
            raise HTTPException(404, "固定几何节流记录不存在。")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/operating-throttle/{throttle_id}/cancel")
    async def cancel_operating_throttle(throttle_id: str, request: Request):
        operating_throttle_record(throttle_id, request)
        return await cancel(throttle_id, request)

    @app.get("/api/operating-throttle/{throttle_id}/download")
    def download_operating_throttle(throttle_id: str, request: Request):
        record = operating_throttle_record(throttle_id, request)
        if record["result"] is None:
            raise HTTPException(409, "此扫描尚无可导出的结果。")
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="lre-operating-throttle-{record["id"]}.json"'})

    @app.get("/api/projects/{project_id}/throttles")
    def throttles(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
                         "j.progress,j.error,j.inputs,u.display_name AS author FROM jobs j "
                         "JOIN users u ON u.id=j.submitted_by WHERE j.project_id=? AND j.kind='throttle' "
                         "ORDER BY j.created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["kind"] = "throttle"
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/throttles")
    async def submit_throttle(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        raise HTTPException(410, "旧设计匹配扫描已停止新建，仅保留历史记录。请使用工况与运行中的固定几何节流分析。")

    @app.get("/api/throttles/{throttle_id}")
    def throttle_record(throttle_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j JOIN users u ON u.id=j.submitted_by "
                        "WHERE j.id=? AND j.kind='throttle'", (throttle_id,))
        if not row:
            raise HTTPException(404, "节流扫描记录不存在。")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/throttles/{throttle_id}/cancel")
    async def cancel_throttle(throttle_id: str, request: Request):
        throttle_record(throttle_id, request)
        return await cancel(throttle_id, request)

    @app.get("/api/throttles/{throttle_id}/download")
    def download_throttle(throttle_id: str, request: Request):
        record = throttle_record(throttle_id, request)
        if record["result"] is None:
            raise HTTPException(409, "此扫描尚无可导出的结果。")
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="lre-throttle-{record["id"]}.json"'})

    @app.get("/api/projects/{project_id}/transients/setup")
    def transient_configuration(project_id: str, source_job_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        row = store.one("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                        "AND status='succeeded' AND result IS NOT NULL", (source_job_id, project_id))
        if not row:
            raise HTTPException(422, "请选择本项目已完成的稳态设计记录。")
        row["inputs"], row["result"] = json.loads(row["inputs"]), json.loads(row["result"])
        from webapp.transient import transient_setup
        return transient_setup(row)

    @app.get("/api/projects/{project_id}/transients")
    def transients(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
                         "j.progress,j.error,j.inputs,u.display_name AS author FROM jobs j "
                         "JOIN users u ON u.id=j.submitted_by WHERE j.project_id=? AND j.kind='transient' "
                         "ORDER BY j.created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["kind"] = "transient"
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/transients")
    async def submit_transient(project_id: str, request: Request):
        user = session_user(request)
        try:
            value = await request.json()
            if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
                raise ValueError("请选择已完成的来源计算记录。")
            uuid.UUID(value["source_job_id"])
        except (ValueError, AttributeError, RecursionError) as exc:
            raise HTTPException(422, "瞬态请求 JSON 或来源记录标识无效。") from exc
        from webapp.transient import normalize_request
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                             "AND status='succeeded' AND result IS NOT NULL",
                             (value["source_job_id"], project_id)).fetchone()
            if not row:
                raise HTTPException(422, "来源记录必须是本项目已完成的稳态设计计算。")
            source = dict(row)
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
            try:
                normalized = normalize_request(value, source)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                detail = str(exc)[:1600].encode("utf-8", "backslashreplace").decode("utf-8")
                raise HTTPException(422, detail)
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')",
                             (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务。")
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                       "VALUES(?,?,?,?,?,?,?,'transient')", (key, project_id, source["revision"], user["id"],
                        "queued", json.dumps(normalized, ensure_ascii=False, allow_nan=False), now))
        return {"id": key, "status": "queued"}

    @app.get("/api/transients/{transient_id}")
    def transient_record(transient_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j JOIN users u ON u.id=j.submitted_by "
                        "WHERE j.id=? AND j.kind='transient'", (transient_id,))
        if not row:
            raise HTTPException(404, "瞬态分析记录不存在。")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/transients/{transient_id}/cancel")
    async def cancel_transient(transient_id: str, request: Request):
        transient_record(transient_id, request)
        return await cancel(transient_id, request)

    @app.get("/api/transients/{transient_id}/download")
    def download_transient(transient_id: str, request: Request):
        record = transient_record(transient_id, request)
        if record["result"] is None:
            raise HTTPException(409, "此扫描尚无可导出的结果。")
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="lre-transient-{record["id"]}.json"'})

    @app.get("/api/projects/{project_id}/feed-studies/setup")
    def feed_network_configuration(project_id: str, source_job_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        row = store.one("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                        "AND status='succeeded' AND result IS NOT NULL", (source_job_id, project_id))
        if not row:
            raise HTTPException(422, "请选择本项目已完成的稳态设计记录。")
        row["inputs"], row["result"] = json.loads(row["inputs"]), json.loads(row["result"])
        from webapp.feed_network import feed_setup
        return feed_setup(row)

    @app.get("/api/projects/{project_id}/feed-studies")
    def feed_studies(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
                         "j.progress,j.error,j.inputs,u.display_name AS author FROM jobs j "
                         "JOIN users u ON u.id=j.submitted_by WHERE j.project_id=? AND j.kind='feed_network' "
                         "ORDER BY j.created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["kind"] = "feed_network"
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/feed-studies")
    async def submit_feed_network(project_id: str, request: Request):
        user = session_user(request)
        try:
            value = await request.json()
            if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
                raise ValueError("请选择已完成的来源计算记录。")
            uuid.UUID(value["source_job_id"])
        except (ValueError, AttributeError, RecursionError) as exc:
            raise HTTPException(422, "供给网络动态请求 JSON 或来源记录标识无效。") from exc
        from webapp.feed_network import normalize_request
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                             "AND status='succeeded' AND result IS NOT NULL",
                             (value["source_job_id"], project_id)).fetchone()
            if not row:
                raise HTTPException(422, "来源记录必须是本项目已完成的稳态设计计算。")
            source = dict(row)
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
            try:
                normalized = normalize_request(value, source)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                detail = str(exc)[:1600].encode("utf-8", "backslashreplace").decode("utf-8")
                raise HTTPException(422, detail)
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')",
                             (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务。")
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                       "VALUES(?,?,?,?,?,?,?,'feed_network')", (key, project_id, source["revision"], user["id"],
                        "queued", json.dumps(normalized, ensure_ascii=False, allow_nan=False), now))
        return {"id": key, "status": "queued"}

    @app.get("/api/feed-studies/{feed_network_id}")
    def feed_network_record(feed_network_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j JOIN users u ON u.id=j.submitted_by "
                        "WHERE j.id=? AND j.kind='feed_network'", (feed_network_id,))
        if not row:
            raise HTTPException(404, "供给网络动态分析记录不存在。")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/feed-studies/{feed_network_id}/cancel")
    async def cancel_feed_network(feed_network_id: str, request: Request):
        feed_network_record(feed_network_id, request)
        return await cancel(feed_network_id, request)

    @app.get("/api/feed-studies/{feed_network_id}/download")
    def download_feed_network(feed_network_id: str, request: Request):
        record = feed_network_record(feed_network_id, request)
        if record["result"] is None:
            raise HTTPException(409, "此扫描尚无可导出的结果。")
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="lre-feed_network-{record["id"]}.json"'})

    @app.get("/api/projects/{project_id}/chilldown-studies/setup")
    def chilldown_configuration(project_id: str, source_job_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        row = store.one("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                        "AND status='succeeded' AND result IS NOT NULL", (source_job_id, project_id))
        if not row:
            raise HTTPException(422, "请选择本项目已完成的稳态设计记录。")
        row["inputs"], row["result"] = json.loads(row["inputs"]), json.loads(row["result"])
        from webapp.chilldown import chilldown_setup
        return chilldown_setup(row)

    @app.get("/api/projects/{project_id}/chilldown-studies")
    def feed_studies(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
                         "j.progress,j.error,j.inputs,u.display_name AS author FROM jobs j "
                         "JOIN users u ON u.id=j.submitted_by WHERE j.project_id=? AND j.kind='chilldown' "
                         "ORDER BY j.created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["kind"] = "chilldown"
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/chilldown-studies")
    async def submit_chilldown(project_id: str, request: Request):
        user = session_user(request)
        try:
            value = await request.json()
            if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
                raise ValueError("请选择已完成的来源计算记录。")
            uuid.UUID(value["source_job_id"])
        except (ValueError, AttributeError, RecursionError) as exc:
            raise HTTPException(422, "预冷与充填请求 JSON 或来源记录标识无效。") from exc
        from webapp.chilldown import normalize_request
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                             "AND status='succeeded' AND result IS NOT NULL",
                             (value["source_job_id"], project_id)).fetchone()
            if not row:
                raise HTTPException(422, "来源记录必须是本项目已完成的稳态设计计算。")
            source = dict(row)
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
            try:
                normalized = normalize_request(value, source)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                detail = str(exc)[:1600].encode("utf-8", "backslashreplace").decode("utf-8")
                raise HTTPException(422, detail)
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')",
                             (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务。")
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                       "VALUES(?,?,?,?,?,?,?,'chilldown')", (key, project_id, source["revision"], user["id"],
                        "queued", json.dumps(normalized, ensure_ascii=False, allow_nan=False), now))
        return {"id": key, "status": "queued"}

    @app.get("/api/chilldown-studies/{chilldown_id}")
    def chilldown_record(chilldown_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j JOIN users u ON u.id=j.submitted_by "
                        "WHERE j.id=? AND j.kind='chilldown'", (chilldown_id,))
        if not row:
            raise HTTPException(404, "预冷与充填分析记录不存在。")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/chilldown-studies/{chilldown_id}/cancel")
    async def cancel_chilldown(chilldown_id: str, request: Request):
        chilldown_record(chilldown_id, request)
        return await cancel(chilldown_id, request)

    @app.get("/api/chilldown-studies/{chilldown_id}/download")
    def download_chilldown(chilldown_id: str, request: Request):
        record = chilldown_record(chilldown_id, request)
        if record["result"] is None:
            raise HTTPException(409, "此扫描尚无可导出的结果。")
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="lre-chilldown-{record["id"]}.json"'})

    @app.get("/api/projects/{project_id}/ignition-studies/setup")
    def ignition_configuration(project_id: str, source_job_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        row = store.one("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                        "AND status='succeeded' AND result IS NOT NULL", (source_job_id, project_id))
        if not row:
            raise HTTPException(422, "请选择本项目已完成的稳态设计记录。")
        row["inputs"], row["result"] = json.loads(row["inputs"]), json.loads(row["result"])
        from webapp.ignition import ignition_setup
        return ignition_setup(row)

    @app.get("/api/projects/{project_id}/ignition-studies")
    def ignition_studies(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
                         "j.progress,j.error,j.inputs,u.display_name AS author FROM jobs j "
                         "JOIN users u ON u.id=j.submitted_by WHERE j.project_id=? AND j.kind='ignition' "
                         "ORDER BY j.created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["kind"] = "ignition"
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/ignition-studies")
    async def submit_ignition(project_id: str, request: Request):
        user = session_user(request)
        try:
            value = await request.json()
            if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
                raise ValueError("请选择已完成的来源计算记录。")
            uuid.UUID(value["source_job_id"])
        except (ValueError, AttributeError, RecursionError) as exc:
            raise HTTPException(422, "点火事件与建压请求 JSON 或来源记录标识无效。") from exc
        from webapp.ignition import normalize_request
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                             "AND status='succeeded' AND result IS NOT NULL",
                             (value["source_job_id"], project_id)).fetchone()
            if not row:
                raise HTTPException(422, "来源记录必须是本项目已完成的稳态设计计算。")
            source = dict(row)
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
            try:
                normalized = normalize_request(value, source)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                detail = str(exc)[:1600].encode("utf-8", "backslashreplace").decode("utf-8")
                raise HTTPException(422, detail)
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')",
                             (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务。")
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                       "VALUES(?,?,?,?,?,?,?,'ignition')", (key, project_id, source["revision"], user["id"],
                        "queued", json.dumps(normalized, ensure_ascii=False, allow_nan=False), now))
        return {"id": key, "status": "queued"}

    @app.get("/api/ignition-studies/{ignition_id}")
    def ignition_record(ignition_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j JOIN users u ON u.id=j.submitted_by "
                        "WHERE j.id=? AND j.kind='ignition'", (ignition_id,))
        if not row:
            raise HTTPException(404, "点火事件与建压分析记录不存在。")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/ignition-studies/{ignition_id}/cancel")
    async def cancel_ignition(ignition_id: str, request: Request):
        ignition_record(ignition_id, request)
        return await cancel(ignition_id, request)

    @app.get("/api/ignition-studies/{ignition_id}/download")
    def download_ignition(ignition_id: str, request: Request):
        record = ignition_record(ignition_id, request)
        if record["result"] is None:
            raise HTTPException(409, "此分析尚无可导出的结果。")
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="lre-ignition-{record["id"]}.json"'})

    @app.get("/api/projects/{project_id}/shutdown-studies/setup")
    def shutdown_configuration(project_id: str, source_job_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        row = store.one("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                        "AND status='succeeded' AND result IS NOT NULL", (source_job_id, project_id))
        if not row:
            raise HTTPException(422, "请选择本项目已完成的稳态设计记录。")
        row["inputs"], row["result"] = json.loads(row["inputs"]), json.loads(row["result"])
        from webapp.shutdown import shutdown_setup
        return shutdown_setup(row)

    @app.get("/api/projects/{project_id}/shutdown-studies")
    def shutdown_studies(project_id: str, request: Request):
        session_user(request)
        get_project(project_id)
        rows = store.all("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
                         "j.progress,j.error,j.inputs,u.display_name AS author FROM jobs j "
                         "JOIN users u ON u.id=j.submitted_by WHERE j.project_id=? AND j.kind='shutdown' "
                         "ORDER BY j.created DESC LIMIT 100", (project_id,))
        for row in rows:
            row["kind"] = "shutdown"
            row["request"] = json.loads(row.pop("inputs"))
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.post("/api/projects/{project_id}/shutdown-studies")
    async def submit_shutdown(project_id: str, request: Request):
        user = session_user(request)
        try:
            value = await request.json()
            if not isinstance(value, dict) or not isinstance(value.get("source_job_id"), str):
                raise ValueError("请选择已完成的来源计算记录。")
            uuid.UUID(value["source_job_id"])
        except (ValueError, AttributeError, RecursionError) as exc:
            raise HTTPException(422, "关机与再启动请求 JSON 或来源记录标识无效。") from exc
        from webapp.shutdown import normalize_request
        key, now = str(uuid.uuid4()), time.time()
        with store.connect(immediate=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND project_id=? AND kind='design' "
                             "AND status='succeeded' AND result IS NOT NULL",
                             (value["source_job_id"], project_id)).fetchone()
            if not row:
                raise HTTPException(422, "来源记录必须是本项目已完成的稳态设计计算。")
            source = dict(row)
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
            try:
                normalized = normalize_request(value, source)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                detail = str(exc)[:1600].encode("utf-8", "backslashreplace").decode("utf-8")
                raise HTTPException(422, detail)
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            own = db.execute("SELECT COUNT(*) FROM jobs WHERE submitted_by=? AND status IN ('queued','running')",
                             (user["id"],)).fetchone()[0]
            if count >= 12 or own >= 3:
                raise HTTPException(429, "等待任务过多，请先等待完成或取消已有任务。")
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created,kind) "
                       "VALUES(?,?,?,?,?,?,?,'shutdown')", (key, project_id, source["revision"], user["id"],
                        "queued", json.dumps(normalized, ensure_ascii=False, allow_nan=False), now))
        return {"id": key, "status": "queued"}

    @app.get("/api/shutdown-studies/{shutdown_id}")
    def shutdown_record(shutdown_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j JOIN users u ON u.id=j.submitted_by "
                        "WHERE j.id=? AND j.kind='shutdown'", (shutdown_id,))
        if not row:
            raise HTTPException(404, "关机与再启动分析记录不存在。")
        row["request"] = json.loads(row.pop("inputs"))
        for field in ("result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

    @app.post("/api/shutdown-studies/{shutdown_id}/cancel")
    async def cancel_shutdown(shutdown_id: str, request: Request):
        shutdown_record(shutdown_id, request)
        return await cancel(shutdown_id, request)

    @app.get("/api/shutdown-studies/{shutdown_id}/download")
    def download_shutdown(shutdown_id: str, request: Request):
        record = shutdown_record(shutdown_id, request)
        if record["result"] is None:
            raise HTTPException(409, "此分析尚无可导出的结果。")
        return JSONResponse(record, headers={
            "Content-Disposition": f'attachment; filename="lre-shutdown-{record["id"]}.json"'})

    @app.get("/")
    def index():
        return FileResponse(ROOT / "webapp/static/index.html")

    @app.get("/static/{filename}")
    def static_asset(filename: str):
        # Explicit media types also cover imported modules on Windows, where
        # registry associations can incorrectly classify JavaScript as text/plain.
        media_types = {"bootstrap.js": "text/javascript", "app.js": "text/javascript", "results.js": "text/javascript",
                       "navigation.js": "text/javascript", "navigation.css": "text/css",
                       "partitioned-workspace.css": "text/css",
                       "parameter-editor.js": "text/javascript", "design-readiness.js": "text/javascript",
                       "preburner-design.js": "text/javascript",
                       "pressure-design.js": "text/javascript", "pressure-design.css": "text/css",
                       "design-review-migration.js": "text/javascript",
                       "nozzle-geometry.js": "text/javascript", "nozzle-geometry.css": "text/css",
                       "nozzle-design.js": "text/javascript", "nozzle-design.css": "text/css",
                       "chamber-design.js": "text/javascript", "chamber-design.css": "text/css",
                       "nozzle-export.js": "text/javascript",
                       "pump-geometry.js": "text/javascript", "pump-geometry.css": "text/css",
                       "injector-design.js": "text/javascript", "injector-design.css": "text/css",
                       "turbine-geometry.js": "text/javascript", "turbine-geometry.css": "text/css",
                       "turbine-design.js": "text/javascript", "turbine-design.css": "text/css",
                       "turbine-preliminary.js": "text/javascript",
                       "result-consistency.js": "text/javascript",
                       "thermo-applicability.js": "text/javascript", "thermo-applicability.css": "text/css",
                       "cooling.js": "text/javascript", "cooling.css": "text/css",
                       "cooling-design.js": "text/javascript", "cooling-design.css": "text/css",
                       "cooling-channel-inputs.js": "text/javascript", "cooling-channel-inputs.css": "text/css",
                       "cooling-input-summary.js": "text/javascript",
                       "convergence.js": "text/javascript", "convergence.css": "text/css",
                       "steady-closure.js": "text/javascript", "steady-closure.css": "text/css",
                       "steady-workspace.js": "text/javascript", "steady-workspace.css": "text/css",
                       "steady-design.js": "text/javascript", "concept-design.js": "text/javascript",
                       "component-analysis.js": "text/javascript", "component-analysis.css": "text/css",
                       "cycle-model.js": "text/javascript", "cycle-schematic.js": "text/javascript", "cycle-schematic.css": "text/css",
                       "cycle-symbols.js": "text/javascript",
                       "flow-model.js": "text/javascript", "flow-analysis.js": "text/javascript", "flow-analysis.css": "text/css",
                       "validation.js": "text/javascript", "validation.css": "text/css",
                       "gas-handoff.js": "text/javascript", "gas-handoff.css": "text/css",
                       "thermo-stations.js": "text/javascript",
                       "throttle.js": "text/javascript", "throttle.css": "text/css",
                       "fixed-steady.js": "text/javascript", "fixed-steady.css": "text/css",
                       "fixed-iteration.js": "text/javascript", "valve-requirements.js": "text/javascript",
                       "control-trim.js": "text/javascript",
                       "transient.js": "text/javascript", "transient.css": "text/css",
                       "feed-network.js": "text/javascript", "feed-network.css": "text/css",
                       "chilldown.js": "text/javascript", "chilldown.css": "text/css",
                       "ignition.js": "text/javascript", "ignition.css": "text/css",
                       "shutdown.js": "text/javascript", "shutdown.css": "text/css",
                       "validation-charts.js": "text/javascript", "validation-charts.css": "text/css",
                       "property-comparison.js": "text/javascript", "property-comparison.css": "text/css",
                       "style.css": "text/css", "workbench.css": "text/css"}
        if filename not in media_types:
            raise HTTPException(404, "资源不存在。")
        return FileResponse(ROOT / "webapp/static" / filename, media_type=media_types[filename])
    return app
