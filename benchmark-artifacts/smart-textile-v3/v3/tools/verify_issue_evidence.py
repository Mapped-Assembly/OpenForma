"""Validate declarative architecture and render the native electrical interface model."""
from __future__ import annotations
import copy
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from pydantic import ValidationError
from forma_core.workspaces.projects.models import SystemArchitecture
from forma_core.agents.system_architecture import architecture_tree_is_usable
from forma_core.electronics.models import ElectricalIR
from forma_core.utils import generate_svg_schematic


def verify(root: Path) -> None:
    """Export native schema and exact endpoint coverage evidence; no physical claims."""
    raw=json.loads((root/'system-architecture.json').read_text())
    architecture=SystemArchitecture.model_validate(raw)
    assert architecture_tree_is_usable(architecture)
    bad=copy.deepcopy(raw)
    bad['root']['children'][0]['children']=[-1,0,1,2]
    bad['root']['children'][1]['children']=[3,4]
    bad['root']['children'][2]['children']=[5,6,7,8]
    try:
        SystemArchitecture.model_validate(bad)
    except ValidationError as exc:
        errors=[{'location':list(e['loc']),'type':e['type']} for e in exc.errors()]
    else:
        raise AssertionError('Integer children incorrectly accepted')
    assert len(errors)==10
    electrical=ElectricalIR.model_validate_json((root.parent/'electrical.json').read_text())
    svg=generate_svg_schematic(electrical)
    (root/'electrical-interface.svg').write_text(svg)
    tree=ET.fromstring(svg)
    observed=set()
    for component in tree.iter():
        ref=component.attrib.get('data-component')
        if ref:
            for pin in component.iter():
                if 'data-pin' in pin.attrib:observed.add((ref,pin.attrib['data-pin'],pin.attrib['data-net']))
    expected={(p.ref_des,p.pin_id,n.net_id) for n in electrical.nets for p in n.pins}
    assert expected<=observed
    result={'architecture_valid':True,'original_integer_child_pattern_rejected':True,'integer_child_error_count':len(errors),'integer_child_errors':errors,'electrical_components':len(electrical.components),'nets':len(electrical.nets),'net_endpoints':len(expected),'all_net_endpoints_rendered':True,'claim':'Validation and rendering in pinned OpenForma checkout only; not a reproduction or fix in the CAID deployment.'}
    (root/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    verify(Path(sys.argv[1]).resolve())
