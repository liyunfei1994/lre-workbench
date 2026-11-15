"""Authenticated, read-only property inspection endpoints."""
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from lre_workbench.properties.library import property_catalog, sample_property_curve


class PropertyCurveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["species", "fluid", "material"]
    dataset: str = Field(min_length=1, max_length=100)
    member: str = Field(default="", max_length=100)
    start_K: float = Field(gt=0, strict=True)
    end_K: float = Field(gt=0, strict=True)
    points: int = Field(default=61, ge=2, le=121, strict=True)
    pressure_Pa: float | None = Field(default=None, gt=0, strict=True)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def register_property_routes(app, session_user, body):
    @app.get("/api/property-library")
    def catalog(request: Request):
        session_user(request)
        return property_catalog()

    @app.post("/api/property-library/curve")
    async def curve(request: Request):
        session_user(request)
        values = await body(request, PropertyCurveRequest)
        try:
            return sample_property_curve(**values.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
