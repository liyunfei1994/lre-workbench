"""Loopback-first authenticated workspace API and local static interface."""
from contextlib import asynccontextmanager
import asyncio
import hashlib
import hmac
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from webapp.store import Store
from webapp.jobs import JobQueue
from webapp.solver import input_values, presets, SCIENCE_PINS

ROOT = Path(__file__).resolve().parent.parent
COOKIE = "lre_session"
MAX_BODY = 200_000


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Model):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=1, max_length=128)


class Registration(Login):
    display_name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=10, max_length=128)


class Project(Model):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    inputs: dict
    revision: int | None = Field(default=None, ge=1)


class Submit(Model):
    revision: int = Field(ge=1)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
    return salt + ":" + value.hex()


def public_user(row):
    return {key: row[key] for key in ("id", "username", "display_name", "role")}


def create_app(database=None, *, queue_enabled=True, job_timeout=600):
    store = Store(database or os.environ.get(
        "LRE_WEB_DB", str(ROOT / "user_data/web-workbench/workspace.sqlite3")))
    queue = JobQueue(store, timeout=job_timeout)

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
                  redoc_url=None, openapi_url=None)
    app.state.store, app.state.queue = store, queue
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            # Browser writes must be same-origin JSON; never accept a cross-site
            # form, a missing Origin, or an unbounded streaming request body.
            origin = request.headers.get("origin")
            if origin != str(request.base_url).rstrip("/"):
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
        except (ValueError, ValidationError):
            raise HTTPException(422, "输入格式不正确，请检查字段、长度和数值。")

    def session_user(request, required=True):
        token = request.cookies.get(COOKIE, "")
        row = store.one(
            "SELECT u.* FROM sessions s JOIN users u ON s.user_id=u.id "
            "WHERE s.token_hash=? AND s.expires>?", (digest(token), time.time())) if token else None
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
        with store.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), row["id"], now + 12*3600))
        response = JSONResponse(public_user(row))
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            secure=False, max_age=12*3600, path="/")
        return response

    def get_project(project_id):
        row = store.one(
            "SELECT p.*,u.display_name AS author FROM projects p "
            "JOIN users u ON u.id=p.updated_by WHERE p.id=?", (project_id,))
        if not row:
            raise HTTPException(404, "项目不存在。")
        row["inputs"] = json.loads(row["inputs"])
        return row

    def normalize_project(data):
        try:
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
                "workspace_id": digest(str(store.path))}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        row = session_user(request, required=False)
        return {"initialized": bool(store.one("SELECT id FROM users LIMIT 1")),
                "user": public_user(row) if row else None}

    @app.post("/api/auth/setup")
    async def setup(request: Request):
        if request.client.host not in ("127.0.0.1", "::1"):
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
        return set_session(row)

    @app.post("/api/auth/login")
    async def login(request: Request):
        data = await body(request, Login)
        # A shared per-client budget also bounds attempts against unknown users.
        key = digest(request.client.host)
        now = time.time()
        with store.connect(immediate=True) as db:
            db.execute("DELETE FROM login_limits WHERE until_time<?", (now,))
            limit = db.execute("SELECT attempts FROM login_limits WHERE key=?", (key,)).fetchone()
            if limit and limit["attempts"] >= 10:
                raise HTTPException(429, "登录尝试过多，请 15 分钟后再试。")
            db.execute("INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(key) "
                       "DO UPDATE SET attempts=attempts+1", (key, now+900))
        row = store.one("SELECT * FROM users WHERE username=?", (data.username.lower(),))
        encoded = await asyncio.to_thread(password_hash, data.password,
                                          row["password_hash"].split(":")[0] if row else "00"*16)
        if not row or not hmac.compare_digest(row["password_hash"], encoded):
            raise HTTPException(401, "用户名或密码不正确。")
        store.execute("DELETE FROM login_limits WHERE key=?", (key,))
        return set_session(row)

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        store.execute("DELETE FROM sessions WHERE token_hash=?", (digest(request.cookies.get(COOKIE, "")),))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/members")
    def members(request: Request):
        administrator(request)
        return store.all("SELECT id,username,display_name,role,created FROM users ORDER BY created")

    @app.post("/api/members")
    async def create_member(request: Request):
        administrator(request)
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
        return {"python": os.sys.version.split()[0], "scope": "local",
                "max_parallel_jobs": 1, "job_timeout_seconds": job_timeout,
                "presets": presets(),
                "dependencies": {k: importlib.metadata.version(k) for k in SCIENCE_PINS}}

    @app.get("/api/projects")
    def projects(request: Request):
        session_user(request)
        return store.all(
            "SELECT p.id,p.name,p.description,p.revision,p.created,p.updated,u.display_name AS author,"
            "(SELECT COUNT(*) FROM jobs j WHERE j.project_id=p.id) AS job_count "
            "FROM projects p JOIN users u ON u.id=p.updated_by ORDER BY p.updated DESC")

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
        return {"project": get_project(key), "warnings": messages}

    @app.get("/api/projects/{project_id}")
    def project(project_id: str, request: Request):
        session_user(request)
        return get_project(project_id)

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
            db.execute("INSERT INTO jobs(id,project_id,revision,submitted_by,status,inputs,created) "
                       "VALUES(?,?,?,?,?,?,?)",
                       (key, project_id, data.revision, user["id"], "queued", row["inputs"], now))
        return {"id": key, "status": "queued"}

    @app.get("/api/jobs")
    def jobs(request: Request, project_id: str | None = None):
        session_user(request)
        sql = ("SELECT j.id,j.project_id,j.revision,j.submitted_by,j.status,j.created,j.started,j.finished,"
               "j.progress,j.error,p.name AS project_name,u.display_name AS author FROM jobs j "
               "JOIN projects p ON p.id=j.project_id JOIN users u ON u.id=j.submitted_by ")
        rows = store.all(sql + ("WHERE j.project_id=? " if project_id else "") + "ORDER BY j.created DESC LIMIT 100",
                         (project_id,) if project_id else ())
        for row in rows:
            row["progress"] = json.loads(row["progress"]) if row["progress"] else None
        return rows

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, request: Request):
        session_user(request)
        row = store.one("SELECT j.*,u.display_name AS author FROM jobs j "
                        "JOIN users u ON u.id=j.submitted_by WHERE j.id=?", (job_id,))
        if not row:
            raise HTTPException(404, "任务不存在。")
        for field in ("inputs", "result", "progress"):
            row[field] = json.loads(row[field]) if row[field] else None
        return row

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

    @app.get("/")
    def index():
        return FileResponse(ROOT / "webapp/static/index.html")

    @app.get("/static/app.js")
    def javascript():
        # Windows registry associations can classify .js as text/plain.
        return FileResponse(ROOT / "webapp/static/app.js", media_type="text/javascript")

    @app.get("/static/style.css")
    def stylesheet():
        return FileResponse(ROOT / "webapp/static/style.css", media_type="text/css")

    app.mount("/static", StaticFiles(directory=ROOT / "webapp/static"), name="static")
    return app
