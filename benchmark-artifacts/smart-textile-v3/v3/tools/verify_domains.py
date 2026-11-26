"""Run OpenForma's existing domain validators and analysis APIs on JSON inputs."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Any
from forma_core.domains.registry import parse_domain, validate_domain


def inspect(path: Path) -> dict[str, Any]:
    """Return native findings and supported engineering-analysis receipts."""
    payload = json.loads(path.read_text())
    result: dict[str, Any] = {'file':str(path),'findings':[f.model_dump(mode='json') for f in validate_domain(payload)]}
    try:
        document = parse_domain(payload)
    except ValueError as exc:
        result['parse_error']=str(exc)
        return result
    if document.domain == 'electrical':
        from forma_core.electronics.power import power_budget
        result['power_budget']={key:value.model_dump(mode='json') for key,value in power_budget(document).items()}
    elif document.domain == 'thermal':
        from forma_core.thermal.validation import analyze_thermal
        result['thermal_analysis']=analyze_thermal(document).model_dump(mode='json')
    return result


if __name__ == '__main__':
    print(json.dumps([inspect(Path(arg)) for arg in sys.argv[1:]],indent=2))
