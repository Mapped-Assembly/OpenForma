from forma_core.electronics.rules import REQUIREMENT_CAPABILITIES, validate_circuit, validate_requirement_coverage
from typing import List, Dict, Set, Optional
from forma_core.firmware.validation import firmware_target_rules
from forma_core.fluidics import FluidicSystem, validate_fluidic_system
from forma_core.workspaces.projects.models import (
    ComponentInstance,
    ConnectionNet,
    FunctionalRequirements,
    HardwareIntermediateRepresentation,
    PinDefinition,
    ValidationIssue,
    ValidationSummary,
)


def check_safety_violations(prompt: str) -> Optional[str]:
    """
    Checks if the user's prompt contains safety violations that fall outside MVP scope.
    Specifically blocks/warns on: mains AC systems, medical devices, automotive systems, weapons, high-power battery systems.
    """
    prompt_lower = prompt.lower()
    
    # 1. Weapons
    weapon_keywords = ["weapon", "gun", "firearm", "missile", "explosive", "grenade", "bomb", "defense system", "tactical military", "ammunition", "artillery", "pistol"]
    for word in weapon_keywords:
        if word in prompt_lower:
            return f"Safety Block: Weapons-related projects ('{word}') are strictly blocked. Forma only supports educational, hobbyist, and safe IoT hardware prototypes."
            
    # 2. Medical Devices
    medical_keywords = ["medical", "pacemaker", "ventilator", "life support", "implant", "clinical health", "surgical", "life-support", "dialysis", "biomedical"]
    for word in medical_keywords:
        if word in prompt_lower:
            return f"Safety Block: Critical medical or life-support devices ('{word}') are strictly blocked. Forma only generates low-voltage educational prototypes and does not compile medical grade electronics."

    # 3. Automotive Systems
    automotive_keywords = ["automotive", "car system", "ecu control", "engine control", "vehicle safety", "brake control", "can-bus car", "throttle control", "autopilot car"]
    for word in automotive_keywords:
        if word in prompt_lower:
            return f"Safety Block: High-risk automotive vehicle control systems ('{word}') are blocked to prevent unsafe driving automation prototypes."

    # 4. Mains AC
    mains_keywords = ["mains ac", "110v", "220v", "ac mains", "outlet power", "wall plug ac", "high voltage ac", "240v", "ac outlet", "wall socket"]
    for word in mains_keywords:
        if word in prompt_lower:
            return f"Safety Warning: Projects switching mains AC electricity (110V-240V) are explicitly blocked. Please modify your prompt to use low-voltage DC relays (e.g. switching 5V or 12V DC elements) for electrical safety."

    # 5. High-Power Batteries
    battery_keywords = ["high-power battery", "high power battery", "tesla pack", "48v battery", "60v battery", "high voltage lithium", "ev battery", "electric vehicle battery"]
    for word in battery_keywords:
        if word in prompt_lower:
            return f"Safety Warning: High-power battery packs and electric vehicle charging systems are blocked due to extreme fire and electrical hazards. Please focus on low-voltage battery setups (such as standard 3.7V LiPo or AA cells)."

    return None


def validate_fluidics(system: FluidicSystem | None) -> List[ValidationIssue]:
    """Adapt fluid-network diagnostics to the existing project validation contract."""
    if system is None:
        return []
    return [ValidationIssue(
        severity="CRITICAL", category=f"Fluidics: {issue.code}",
        description=f"{issue.entity_id}: {issue.message}",
        troubleshooting="Correct the fluidic_system references or supplied operating constraints and revalidate.",
    ) for issue in validate_fluidic_system(system)]


def validate_project(project: HardwareIntermediateRepresentation) -> List[ValidationIssue]:
    """Validate the existing electrical contract plus optional fluidics and firmware targets."""
    return [
        *validate_circuit(project.components, project.nets, project.requirements),
        *validate_fluidics(project.fluidic_system),
        *firmware_target_rules(project),
    ]


def build_validation_summary(issues: List[ValidationIssue]) -> ValidationSummary:
    """
    Groups a list of individual ValidationIssue models into critical, warning, and info lists.
    """
    critical = [issue for issue in issues if issue.severity.upper() == "CRITICAL"]
    warning = [issue for issue in issues if issue.severity.upper() == "WARNING"]
    info = [issue for issue in issues if issue.severity.upper() == "INFO"]
    return ValidationSummary(critical=critical, warning=warning, info=info)
