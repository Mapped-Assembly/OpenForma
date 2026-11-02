"""Legacy diagnostic contracts shared by domain and compatibility validators."""
from typing import List
from pydantic import BaseModel, Field

class ValidationIssue(BaseModel):
    severity: str = Field(..., description="Severity level: CRITICAL, WARNING, or INFO")
    category: str = Field(..., description="Short circuit, Voltage Mismatch, Unpowered IC, Pin Conflict, Overcurrent, Safety Block")
    description: str = Field(..., description="Detailed description of the validation issue")
    troubleshooting: str = Field(..., description="Suggested remediation action for self-healing or user override")


class ValidationSummary(BaseModel):
    critical: List[ValidationIssue] = Field(default_factory=list, description="Critical blocking issues or errors")
    warning: List[ValidationIssue] = Field(default_factory=list, description="Warning level issues")
    info: List[ValidationIssue] = Field(default_factory=list, description="Informational recommendations")
