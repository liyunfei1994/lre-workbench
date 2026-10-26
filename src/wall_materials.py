"""Small, source-specific solid-wall thermal dataset registry (SI units).

Material identity, specimen manufacturing state and property dataset identity are
deliberately separate. These records are thermal model inputs, not structural
allowables, fatigue qualifications, or fluid/thermochemical data.

``policy='endpoint'`` continues the nearest endpoint as a constant outside the
source interval, solely to let nonlinear trial evaluations proceed. It does not
extend the data's validity. Call ``audit_interval`` on every accepted wall's full
temperature interval and save that audit with the result. ``policy='strict'``
rejects any out-of-range evaluation. Neither policy clips the wall temperature.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Literal


RangePolicy = Literal["endpoint", "strict"]
GRCOP84_DATASET_ID = "grcop84_ellis2000_extruded_high_fe_v1"
CUZR_DATASET_ID = "cuzr_qi2024_heat_treated_v1"


class MaterialPropertyRangeError(ValueError):
    """A strict property request lies outside its documented temperature range."""


def _temperature(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("Material temperature must be finite and positive in kelvin")
    return value


def _policy(value: str) -> RangePolicy:
    if value not in ("endpoint", "strict"):
        raise ValueError("Material range policy must be 'endpoint' or 'strict'")
    return value  # type: ignore[return-value]


@dataclass(frozen=True)
class ThermalProperty:
    """One bounded property; coefficients use kelvin, values use ``units``."""

    name: str
    units: str
    range_K: tuple[float, float]
    representation: str
    coefficients: tuple[float, ...] = ()
    points: tuple[tuple[float, float], ...] = ()
    source_locations: tuple[str, ...] = ()
    statistic: str = "published_measurements"

    def _inside_value(self, temperature: float) -> float:
        if self.representation == "log_polynomial":
            coordinate = math.log(temperature)
        elif self.representation == "polynomial":
            coordinate = temperature
        elif self.representation == "piecewise_linear":
            i = min(max(bisect_right(self.points, (temperature, math.inf))-1, 0),
                    len(self.points)-2)
            t0, v0 = self.points[i]
            t1, v1 = self.points[i+1]
            return v0 + (v1-v0)*(temperature-t0)/(t1-t0)
        else:
            raise ValueError(f"Unknown property representation: {self.representation}")
        result = 0.0
        for coefficient in reversed(self.coefficients):
            result = result*coordinate + coefficient
        return result

    def value(self, temperature: float, *, policy: RangePolicy = "endpoint") -> float:
        temperature = _temperature(temperature)
        _policy(policy)
        low, high = self.range_K
        if policy == "strict" and not low <= temperature <= high:
            raise MaterialPropertyRangeError(
                f"{self.name}: {temperature:g} K outside [{low:g}, {high:g}] K")
        return self._inside_value(min(high, max(low, temperature)))

    def _primitive(self, temperature: float) -> float:
        if self.representation == "polynomial":
            return math.fsum(c*temperature**(i+1)/(i+1)
                             for i, c in enumerate(self.coefficients))
        if self.representation == "log_polynomial":
            # Integral of log(T)^n is T * sum((-1)^j n!/(n-j)! log(T)^(n-j)).
            log_t = math.log(temperature)
            terms = []
            for n, coefficient in enumerate(self.coefficients):
                polynomial = math.fsum(
                    (-1)**j * math.factorial(n)/math.factorial(n-j) * log_t**(n-j)
                    for j in range(n+1))
                terms.append(coefficient*temperature*polynomial)
            return math.fsum(terms)
        raise ValueError("A tabulated curve is integrated segment by segment")

    def integral(self, start: float, end: float, *,
                 policy: RangePolicy = "endpoint") -> float:
        """Signed integral, including explicit constant-endpoint continuation.

        For conductivity this returns W/m. Its strictly positive derivative in
        the supported datasets gives a monotone conduction potential.
        """
        start, end = _temperature(start), _temperature(end)
        _policy(policy)
        low, high = self.range_K
        a, b = min(start, end), max(start, end)
        if policy == "strict" and (a < low or b > high):
            raise MaterialPropertyRangeError(
                f"{self.name}: [{a:g}, {b:g}] K outside [{low:g}, {high:g}] K")
        if a == b:
            return 0.0
        pieces = []
        if a < low:
            pieces.append((min(b, low)-a)*self._inside_value(low))
        if b > high:
            pieces.append((b-max(a, high))*self._inside_value(high))
        interior_a, interior_b = max(a, low), min(b, high)
        if interior_b > interior_a:
            if self.representation == "piecewise_linear":
                knots = [interior_a] + [t for t, _ in self.points
                                       if interior_a < t < interior_b] + [interior_b]
                pieces.extend((y-x)*(self._inside_value(x)+self._inside_value(y))/2
                              for x, y in zip(knots, knots[1:]))
            elif interior_b-interior_a < 1e-4*(interior_a+interior_b)/2:
                # Avoid cancellation of two large primitives near an isothermal
                # wall. Simpson's rule is positive and accurate on this narrow
                # interval; its error is O(delta_T**5).
                middle = (interior_a+interior_b)/2
                pieces.append((interior_b-interior_a)/6 *
                              (self._inside_value(interior_a) +
                               4*self._inside_value(middle) +
                               self._inside_value(interior_b)))
            else:
                pieces.append(self._primitive(interior_b)-self._primitive(interior_a))
        integral = math.fsum(pieces)
        return integral if start <= end else -integral


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    title: str
    authors: tuple[str, ...]
    year: int
    report_or_journal: str
    url: str
    doi: str | None
    repository_pdf: str
    locations: tuple[str, ...]


@dataclass(frozen=True)
class ManufacturingState:
    state_id: str
    label: str
    preparation: tuple[str, ...]
    unresolved_details: tuple[str, ...]


@dataclass(frozen=True)
class WallThermalDataset:
    dataset_id: str
    revision: str
    material_id: str
    label: str
    composition: str
    manufacturing_state: ManufacturingState
    conductivity_property: ThermalProperty
    heat_capacity_property: ThermalProperty
    sources: tuple[SourceRecord, ...]
    reference_density_kg_m3: float | None = None
    density_basis: str = "not_provided"
    limitations: tuple[str, ...] = ()

    @property
    def conductivity_range_K(self) -> tuple[float, float]:
        return self.conductivity_property.range_K

    @property
    def heat_capacity_range_K(self) -> tuple[float, float]:
        return self.heat_capacity_property.range_K

    def conductivity(self, temperature: float, *, policy: RangePolicy = "endpoint") -> float:
        return self.conductivity_property.value(temperature, policy=policy)

    def heat_capacity(self, temperature: float, *, policy: RangePolicy = "endpoint") -> float:
        return self.heat_capacity_property.value(temperature, policy=policy)

    def conductivity_integral(self, start: float, end: float, *,
                              policy: RangePolicy = "endpoint") -> float:
        return self.conductivity_property.integral(start, end, policy=policy)

    def metadata(self) -> dict:
        """Fresh, JSON-safe provenance snapshot with a stable content hash."""
        document = asdict(self)
        document.update(
            category="solid_wall_thermal_properties",
            default_out_of_range_policy="endpoint",
            out_of_range_meaning="constant_endpoint_continuation_not_validated_data",
            component_manufacturing_match="not_assessed",
            structural_qualification="not_assessed",
        )
        canonical = json.dumps(document, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        document["data_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return json.loads(json.dumps(document, ensure_ascii=False, allow_nan=False))

    def audit_interval(self, T_low: float, T_high: float, *,
                       property: str = "conductivity", policy: RangePolicy = "endpoint") -> dict:
        """Audit all temperatures across an accepted wall, including both faces.

        A strict audit reports rejection rather than raising; strict property
        evaluation raises. No manufacturing match or hardware qualification is
        inferred merely because an interval is inside the data range.
        """
        temperatures = sorted((_temperature(T_low), _temperature(T_high)))
        _policy(policy)
        curves = {"conductivity": self.conductivity_property,
                  "heat_capacity": self.heat_capacity_property}
        if property not in curves:
            raise KeyError(f"Unsupported wall thermal property: {property}")
        curve = curves[property]
        low, high = curve.range_K
        outside = temperatures[0] < low or temperatures[1] > high
        return {
            "dataset_id": self.dataset_id,
            "dataset_revision": self.revision,
            "material_id": self.material_id,
            "manufacturing_state_id": self.manufacturing_state.state_id,
            "component_manufacturing_match": "not_assessed",
            "property": property,
            "actual_temperature_range_K": temperatures,
            "data_temperature_range_K": [low, high],
            "status": "out_of_range" if outside else "within_range",
            "within_range": not outside,
            "below_range_K": max(0.0, low-temperatures[0]),
            "above_range_K": max(0.0, temperatures[1]-high),
            "policy": policy,
            "action": (("reject" if policy == "strict" else "constant_endpoint_continuation")
                       if outside else "none"),
            "structural_qualification": "not_assessed",
        }


_HANDBOOK = SourceRecord(
    "ellis_handbook_2002", "Aerospace Structural Materials Handbook Supplement: GRCop-84",
    ("David L. Ellis",), 2002, "NASA NTRS 20020070630",
    "https://ntrs.nasa.gov/citations/20020070630", None,
    "literatures/material/Aerospace Structural Materials Handbook Supplement GRCop-84.pdf",
    ("PDF pp. 4-5, sections 2.1.3, 2.1.5, 2.2.1",),
)
_ELLIS_ORIGINAL = SourceRecord(
    "ellis_keller_2000", "Thermophysical Properties of GRCop-84",
    ("David L. Ellis", "Dennis J. Keller"), 2000, "NASA/CR-2000-210055",
    "", None,
    "literatures/material/Thermophysical properties and electrical resistivity of GRCop-84 "
    "(Cu–8Cr–4Nb) from cryogenic temperatures to near the melting point.pdf",
    ("PDF p. 5 / printed p. 1: specimen preparation",
     "PDF pp. 9-11 / printed pp. 5-7: thermal regressions"),
)
_QI = SourceRecord(
    "qi_2024", "Thermal-structural analysis for reusable thrust chamber using "
    "temperature-dependent CuZr alloy model adapted to cryogenic to elevated temperature",
    ("Yaqun Qi", "Ping Jin", "Zhiwei Chen", "Junjie Lv", "Guobiao Cai"),
    2024, "Aerospace Science and Technology 145, 108782",
    "https://doi.org/10.1016/j.ast.2023.108782", "10.1016/j.ast.2023.108782",
    "literatures/material/Thermal-structural analysis for reusable thrust chamber using "
    "temperature-dependent CuZr alloy model adapted to cryogenic to elevated temperature.pdf",
    ("PDF p. 2: Table 1 and preparation; p. 4: Tables 3-4",),
)

_DATASETS = {
    GRCOP84_DATASET_ID: WallThermalDataset(
        GRCOP84_DATASET_ID, "1", "grcop84",
        "GRCop-84 · 高 Fe 粉末冶金挤压态 · Ellis/NASA",
        "Cu-8 at.% Cr-4 at.% Nb; Fe 150-250 ppm in the source powder lots",
        ManufacturingState(
            "powder_metallurgy_extruded_high_fe", "粉末冶金挤压态；高 Fe 历史试样",
            ("Five powder lots; three extrusion cans per lot; 15 extruded bars",
             "Area reduction ratio 29.5; Fe 150-250 ppm"),
            ("Additional final heat treatment of every thermal specimen is not stated",
             "No equivalence to LPBF, HIP, low-Fe or the user's manufactured component established"),
        ),
        ThermalProperty(
            "conductivity", "W/(m K)", (80.15, 1173.15), "log_polynomial",
            coefficients=(6893.0, -3466.0, 599.5, -34.18),
            source_locations=("ellis_handbook_2002: PDF p. 4, section 2.1.3",),
            statistic="regression_mean_without_confidence_adjustment",
        ),
        ThermalProperty(
            "heat_capacity", "J/(kg K)", (173.15, 1173.15), "polynomial",
            coefficients=(253.9, 0.6563, -8.903e-4, 4.292e-7),
            source_locations=("ellis_handbook_2002: PDF pp. 4-5, section 2.1.5",),
            statistic="regression_mean_without_confidence_adjustment",
        ),
        (_HANDBOOK, _ELLIS_ORIGINAL), 8620.0,
        "Room-temperature measurement 8620 +/- 80 kg/m3 (95% interval); not a rho(T) curve",
        ("Temperature intervals follow the handbook Celsius limits converted to kelvin",
         "Source mean curve, not a material allowable or lower-confidence design curve",
         "The handbook's lower-Fe example is <20 ppm; no universal low-Fe correction is applied"),
    ),
    CUZR_DATASET_ID: WallThermalDataset(
        CUZR_DATASET_ID, "1", "cuzr", "CuZr · 指定固溶／冷变形／时效态 · Qi 2024",
        "CuZr specimen with 0.2 wt.% Zr (Table 1); not CuCrZr / C18150",
        ManufacturingState(
            "qi2024_solution_cold_work_aged", "固溶＋30% 纵向冷变形＋时效",
            ("Vacuum induction melting at 1200 degC",
             "Solution treatment at 920 degC for 1 h; water cooling",
             "30% longitudinal cold deformation",
             "Aging at 450 degC for 3 h; air cooling"),
            ("Transferability to other CuZr batches or manufacturing routes is not established",),
        ),
        ThermalProperty(
            "conductivity", "W/(m K)", (298.0, 1000.0), "piecewise_linear",
            points=((298.0, 383.0), (500.0, 398.0), (700.0, 369.0),
                    (800.0, 366.0), (1000.0, 346.0)),
            source_locations=("qi_2024: PDF p. 4, Table 3",),
        ),
        ThermalProperty(
            "heat_capacity", "J/(kg K)", (298.0, 1000.0), "piecewise_linear",
            points=((298.0, 376.0), (373.0, 404.0), (474.0, 421.0),
                    (573.0, 418.0), (673.0, 422.0), (773.0, 434.0),
                    (873.0, 452.0), (973.0, 450.0), (1000.0, 454.0)),
            source_locations=("qi_2024: PDF p. 4, Table 4; J/(g K) multiplied by 1000",),
        ),
        (_QI,), None, "No independently documented density curve is imported for this record",
        ("Linear interpolation of the tabulated thermal measurements; no smooth fit or extrapolation",
         "Mechanical low-temperature tests do not extend k/cp data below 298 K",
         "Specific heat retains the published 474 K row, rather than replacing it with 473 K"),
    ),
}


def list_wall_datasets() -> tuple[WallThermalDataset, ...]:
    """Return the small set of computationally supported datasets."""
    return tuple(_DATASETS.values())


def get_wall_dataset(dataset_id: str) -> WallThermalDataset:
    try:
        return _DATASETS[dataset_id]
    except KeyError:
        raise KeyError(f"Unknown solid-wall thermal dataset: {dataset_id!r}") from None


def default_dataset_for_material(material_id: str) -> str:
    """Explicit migration only; never substitute generic copper for other alloys."""
    mapping = {"grcop84": GRCOP84_DATASET_ID, "cuzr": CUZR_DATASET_ID}
    try:
        return mapping[material_id]
    except KeyError:
        raise KeyError(f"No source-backed wall dataset registered for material: {material_id!r}") from None
