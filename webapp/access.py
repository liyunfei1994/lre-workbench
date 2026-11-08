"""Explicit HTTPS origin and per-owner access for an isolated browser pilot."""
import ipaddress
import re
from urllib.parse import urlsplit

from fastapi import HTTPException

LOOPBACK = {"127.0.0.1", "::1"}
PUBLIC_ENDPOINTS = {"/api/health", "/api/auth/status", "/api/auth/setup",
                    "/api/auth/login", "/api/auth/logout", "/api/startup-report"}
JOB_PARAMETERS = {"job_id", "validation_id", "study_id", "throttle_id",
                  "transient_id", "feed_network_id", "chilldown_id",
                  "ignition_id", "shutdown_id"}


def https_origin(value):
    """An exact origin, never a wildcard, URL path or inferred request header."""
    if not value:
        return None
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("校外入口必须是完整的 HTTPS 网址。") from None
    try:
        ipaddress.ip_address(host)
        literal_ip = True
    except ValueError:
        literal_ip = False
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or len(host) > 253 or "." not in host
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in host.split("."))
            or literal_ip or host.endswith(".localhost") or port == 0
            or any(c.isspace() for c in value)):
        raise ValueError("校外入口必须是完整的 HTTPS 域名，不含路径、账号或通配符。")
    return f"https://{host}" + (f":{port}" if port and port != 443 else "")


def owned_projects_sql(alias="p"):
    # Revision 1 is immutable. updated_by changes on admin edits and is NOT ownership.
    return (f"EXISTS (SELECT 1 FROM revisions ownership WHERE ownership.project_id={alias}.id "
            "AND ownership.revision=1 AND ownership.author=?)")


def authorize_resource(request, user, store):
    """Called as a router dependency before every private-workspace endpoint.

    Check parent projects for all calculations, exports and candidate drafts.
    Unknown resource parameters fail closed when future endpoints are added.
    Collection endpoints additionally filter their SQL by owner.
    """
    if user["role"] == "admin":
        return
    params = request.path_params
    project_id = params.get("project_id")
    for name, value in params.items():
        if name in JOB_PARAMETERS:
            job = store.one("SELECT project_id FROM jobs WHERE id=?", (value,))
            if not job:
                raise HTTPException(404, "记录不存在或无权访问。")
            project_id = job["project_id"]
        elif name not in {"project_id", "revision", "candidate_id"}:
            raise HTTPException(403, "此资源尚未开放给试用账号。")
    if params and project_id is None:
        raise HTTPException(403, "此资源尚未开放给试用账号。")
    if project_id is not None and not store.one(
            "SELECT 1 FROM projects p WHERE p.id=? AND " + owned_projects_sql(),
            (project_id, user["id"])):
        raise HTTPException(404, "记录不存在或无权访问。")
