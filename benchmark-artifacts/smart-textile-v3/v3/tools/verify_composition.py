"""Validate a pinned native product bundle and run supported domain analyses."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from forma_core.domains.composition import ProductBundle, validate_product
from forma_core.domains.registry import parse_domain
from forma_core.mechanics.layers import inspect_layers
from forma_core.electronics.power import power_budget
from forma_core.thermal.validation import analyze_thermal


def verify(path: Path) -> None:
    """Export independent structural, power and thermal evidence without filling gaps."""
    bundle=ProductBundle.model_validate_json(path.read_text())
    validation=validate_product(bundle)
    result={'composition':{**validation.model_dump(mode='json'),'is_valid':validation.is_valid}}
    for payload in bundle.artifacts.values():
        doc=parse_domain(payload)
        if doc.domain=='mechanical':result['layers']=inspect_layers(doc)
        if doc.domain=='electrical':result['power_budget']={k:v.model_dump(mode='json') for k,v in power_budget(doc).items()}
        if doc.domain=='thermal':result['thermal_analysis']=analyze_thermal(doc).model_dump(mode='json')
    (path.parent/'engineering-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'composition_valid':result['composition']['is_valid'],'layers':result['layers']}))


if __name__=='__main__':
    verify(Path(sys.argv[1]).resolve())
