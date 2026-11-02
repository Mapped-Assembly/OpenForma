"""Existing domain value types, re-exported by the legacy project model."""

from typing import Any, List, Literal, Mapping, Optional
from pydantic import BaseModel, Field, field_validator
from forma_core.mechanics.mechanism_benchmarks import MechanismBenchmark
from forma_core.mechanics.solid_cad import CadOperation


class MechanicalSource(BaseModel):
    name: str = Field(..., description="Display name of the CAD, enclosure, or fabrication source")
    source_type: str = Field(..., description="Source class: Open STL, Paid STL, Vendor CAD, Reference CAD, or Fabrication Estimate")
    url: str = Field(..., description="Resolvable source URL for the CAD model, enclosure datasheet, or fabrication reference")
    file_formats: List[str] = Field(default_factory=list, description="Known CAD/download formats such as STL, STEP, DXF, or Fusion 360")
    license: Optional[str] = Field(None, description="Source license or commercial availability note")
    estimated_unit_price_usd: float = Field(0.0, description="Estimated CAD download, fabrication, or enclosure unit cost in USD")
    notes: Optional[str] = Field(None, description="How this source should be adapted for the generated design")

class MechanicalVector3(BaseModel):
    x_mm: float = Field(..., description="X-axis measurement in millimeters, where X is project width")
    y_mm: float = Field(..., description="Y-axis measurement in millimeters, where Y is project depth")
    z_mm: float = Field(..., description="Z-axis measurement in millimeters, where Z is project height")

class MechanicalRotation3(BaseModel):
    x_deg: float = Field(0.0, description="Rotation around the X axis in degrees")
    y_deg: float = Field(0.0, description="Rotation around the Y axis in degrees")
    z_deg: float = Field(0.0, description="Rotation around the Z axis in degrees")

class MechanicalPlacement(BaseModel):
    ref_des: str = Field(..., description="Reference designator of the component this placement represents")
    label: Optional[str] = Field(None, description="Display label for the placed component")
    category: Optional[str] = Field(None, description="Component class or placement layer such as Microcontroller, Display, 3D Print, or Mechanical")
    layer: str = Field("electrical", description="Visibility layer: electrical, mechanism, print, enclosure, structural, or misc")
    position: MechanicalVector3 = Field(..., description="Component center position in millimeters relative to the enclosure center")
    size: MechanicalVector3 = Field(..., description="Approximate component envelope size in millimeters")
    orientation_deg: MechanicalRotation3 = Field(default_factory=MechanicalRotation3, description="Euler orientation in degrees around X, Y, and Z")
    mounting_face: Optional[str] = Field(None, description="Face or surface used for mounting, such as front, back, floor, lid, left, or right")
    notes: Optional[str] = Field(None, description="Clearance, fastener, cable routing, or assembly notes for this placement")

class MechanicalSpatialRelationship(BaseModel):
    source_ref_des: str = Field(..., description="Reference designator of the source component")
    target_ref_des: str = Field(..., description="Reference designator of the target component")
    relation: str = Field(..., description="Physical relationship such as centered-above, adjacent-to, mounted-on, aligned-with, or clearance-from")
    axis: Optional[str] = Field(None, description="Dominant axis for the relationship: X, Y, or Z")
    offset_mm: Optional[float] = Field(None, description="Signed offset between components along the dominant axis")
    notes: Optional[str] = Field(None, description="Additional placement or clearance rationale")

class MechanicalMotionIntent(BaseModel):
    motion_id: Optional[str] = Field(None, description="Stable project-local identifier for this motion")
    label: Optional[str] = Field(None, description="Human-readable name for the motion preview")
    type: Literal["revolute", "prismatic", "compliant"] = Field(..., description="Authoring intent. Rigid revolute/prismatic intents are resolved and evaluated by OpenCAD; compliant intent is reserved for future deformation preview.")
    target_ref: str = Field(..., description="Reference designator of the moving placement")
    parent_ref: Optional[str] = Field(None, description="Optional reference designator of the stationary parent")
    axis: Literal["X", "Y", "Z"] = Field("Z", description="Project-space motion axis")
    pivot_mm: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3, description="Project-space pivot [x, y, z] in millimeters")
    min_deg: Optional[float] = Field(None, description="Minimum angular travel for revolute/compliant motion")
    max_deg: Optional[float] = Field(None, description="Maximum angular travel for revolute/compliant motion")
    min_mm: Optional[float] = Field(None, description="Minimum linear travel for prismatic motion")
    max_mm: Optional[float] = Field(None, description="Maximum linear travel for prismatic motion")
    notes: Optional[str] = Field(None, description="Motion intent, stops, clearances, or preview limitations")

    @field_validator("axis", mode="before")
    @classmethod
    def normalize_axis(cls, value: Any) -> str:
        axis = str(value or "Z").strip().upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("Mechanical motion axis must be X, Y, or Z")
        return axis

    @field_validator("pivot_mm", mode="before")
    @classmethod
    def normalize_pivot(cls, value: Any) -> Any:
        if value is None:
            return [0.0, 0.0, 0.0]
        if isinstance(value, Mapping):
            return [
                value.get("x_mm", value.get("x", 0.0)),
                value.get("y_mm", value.get("y", 0.0)),
                value.get("z_mm", value.get("z", 0.0)),
            ]
        return value

class MechanicalNotes(BaseModel):
    cad_operations: List[CadOperation] = Field(default_factory=list, max_length=32, description="Explicit solid CAD in millimeters, applied in order; first operation must add. Use for standalone solids (components/nets may be empty). An empty list retains the legacy enclosure generator.")

    @field_validator("cad_operations")
    @classmethod
    def first_cad_operation_adds(cls, operations):
        if operations and operations[0].operation != "add":
            raise ValueError("The first CAD operation must add a solid.")
        return operations

    physical_form: str = Field(
        "Unspecified",
        description=(
            "Overall product shape and silhouette, such as curved handheld, cylindrical, wearable, folded, "
            "open-frame, or another explicitly requested form; do not assume a rectangular box"
        ),
    )
    enclosure_type: str = Field(..., description="Type of housing: 3D Printed, Off-the-shelf, Custom Acrylic, Waterproof, Acrylic laser cut")
    mounting_guidance: str = Field(..., description="Mounting and standoffs instructions")
    fabrication_details: List[str] = Field(default_factory=list, description="Enclosure dimensions, material recommendations, or printing instructions")
    fabrication_cost_estimate_usd: float = Field(0.0, description="Estimated mechanical fabrication cost in USD, excluding electrical BOM")
    cad_sources: List[MechanicalSource] = Field(default_factory=list, description="CAD, enclosure, and fabrication source records")
    manufacturability_rating: str = Field(..., description="Ease of manufacturing: Easy, Moderate, Challenging")
    render_dimensions: Optional[MechanicalVector3] = Field(None, description="Overall live-render envelope dimensions in millimeters")
    component_placements: List[MechanicalPlacement] = Field(default_factory=list, description="Per-component 3D placements for live Three.js rendering")
    spatial_relationships: List[MechanicalSpatialRelationship] = Field(default_factory=list, description="Physical offsets and alignment relationships between placed components")
    motion_intents: List[MechanicalMotionIntent] = Field(default_factory=list, description="Agent-authored motion intent resolved into OpenCAD kinematic joints during CAD generation")
    mechanism_benchmark: Optional[MechanismBenchmark] = Field(None, description="Bounded mechanism: print_in_place_hinge, monolithic_flexure_hinge, or spur_gear_pair. For two meshing gears use spur_gear_pair (defaults: 20/40 teeth, module 2 mm). CAD generation creates real gear bodies and synchronized OpenCAD poses.")
