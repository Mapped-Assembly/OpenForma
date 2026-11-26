"""Render native CAD meshes with a declared BOM and netlist-derived external wires."""
from __future__ import annotations
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from mpl_toolkits.mplot3d import proj3d


def render(path: Path) -> None:
    """Project native geometry and proposed external net connections; no physics claimed."""
    root=path.parent; request=json.loads(path.read_text())
    meshes=json.loads((root/request['mesh_file']).read_text())
    geometry=json.loads((root/request['geometry_file']).read_text())
    electrical=json.loads((root/request['electrical_file']).read_text())
    objects={o['id']:o for o in geometry['objects'] if o['id'] in meshes}
    offsets={k:np.asarray(v) for k,v in request['translations_mm'].items()}
    centers={k:np.asarray(o['position_mm'])+offsets[k] for k,o in objects.items()}
    fig=plt.figure(figsize=(19,12),facecolor='#f4f7fb')
    ax=fig.add_axes([.01,.12,.66,.78],projection='3d',computed_zorder=False)
    ax.set_facecolor('#f4f7fb')
    for oid,mesh in meshes.items():
        vertices=np.asarray(mesh['vertices']).reshape(-1,3)+offsets[oid]
        faces=np.asarray(mesh['faces']).reshape(-1,3)
        poly=Poly3DCollection(vertices[faces],facecolor=objects[oid]['color'],edgecolor='none',alpha=.48 if oid=='textile-band' else .95,zorder=2)
        ax.add_collection3d(poly)
    refs={c['ref_des']:c['object_id'] for c in electrical['components']}
    wires=[]
    for net in electrical['nets']:
        source=net['pins'][0]
        for index,target in enumerate(net['pins'][1:]):
            a,b=refs[source['ref_des']],refs[target['ref_des']]
            if a==b:continue  # Never draw internal PCB connections as loose wires.
            start,end=centers[a].copy(),centers[b].copy()
            start[2]+=index*2;end[2]+=index*2
            mid=(start+end)/2;mid[1]-=45+index*6
            route=np.asarray([start,mid,end])
            ax.plot(*route.T,color=request['net_colors'][net['net_type']],lw=1.35,alpha=.88,zorder=3)
            wires.append({'wire_id':f"W{len(wires)+1:02}",'net_id':net['net_id'],'net_type':net['net_type'],'from_ref':source['ref_des'],'from_pin':source['pin_id'],'to_ref':target['ref_des'],'to_pin':target['pin_id'],'routing_status':'proposed display route; connector position unknown','route_exploded_mm':route.tolist(),'wire_gauge':None,'cut_length_mm':None})
    points=np.asarray(list(centers.values()));low=points.min(axis=0)-[80,70,130];high=points.max(axis=0)+[80,70,100]
    ax.set(xlim=(low[0],high[0]),ylim=(low[1],high[1]),zlim=(low[2],high[2]))
    ax.set_box_aspect(high-low,zoom=1.18);ax.view_init(elev=24,azim=-65);ax.set_axis_off()
    fig.text(.035,.95,request['title'],fontsize=23,weight='bold',color='#17334b')
    fig.text(.035,.915,'Native OpenCAD solids • exploded display offsets • numbered component groups',fontsize=12,color='#526779')
    # Number bubbles label each BOM group at its representative native component.
    for item in request['bom']:
        visible=[oid for oid in item['object_ids'] if oid in centers]
        if not visible:continue
        for oid in visible[:1]:
            pos=centers[oid];x,y,_=proj3d.proj_transform(*pos,ax.get_proj())
            ax.annotate(f"{item['item']:02}",(x,y),xytext={3:(-40,65),4:(-65,40),5:(-80,10),6:(-75,-25),7:(-45,-60)}.get(item['item'],(10,12)),textcoords='offset points',ha='center',va='center',fontsize=9,weight='bold',color='#17334b',bbox={'boxstyle':'circle,pad=.25','fc':'white','ec':'#7792a6','lw':.8},arrowprops={'arrowstyle':'-','color':'#7792a6','lw':.7},zorder=10)
    fig.text(.69,.88,'BILL OF MATERIALS',fontsize=15,weight='bold',color='#17334b')
    fig.text(.69,.855,'#     QTY    COMPONENT / SUBASSEMBLY',fontsize=10,color='#60788c')
    for index,item in enumerate(request['bom']):
        y=.823-index*.038
        fig.text(.69,y,f"{item['item']:02}     {item['quantity']:>2}     {item['name']}",fontsize=10.8,color='#17334b',weight='bold')
        fig.text(.753,y-.016,item['note'],fontsize=8.7,color='#60788c')
    fig.text(.69,.155,'WIRE HARNESS — PROPOSED',fontsize=12,weight='bold',color='#17334b')
    fig.text(.69,.132,f"{len(wires)} net branches • quantities / lengths not released",fontsize=10,color='#526779')
    for i,(kind,color) in enumerate(request['net_colors'].items()):
        fig.text(.035+i*.15,.105,'━ '+kind,fontsize=12,color=color,weight='bold')
    fig.text(.035,.065,'Wires follow declared electrical nets between modules. PCB-internal traces are omitted.',fontsize=12,color='#17334b')
    fig.text(.035,.038,'Proposed display routes only: connector locations, wire gauges, cut lengths and strain relief remain unverified.',fontsize=11,color='#60788c')
    fig.savefig(root/request['output'],dpi=160,facecolor=fig.get_facecolor());plt.close(fig)
    with (root/'bom.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['item','name','quantity','object_ids','note']);writer.writeheader()
        writer.writerows([{**item,'object_ids':';'.join(item['object_ids'])} for item in request['bom']])
    (root/'external-wires.json').write_text(json.dumps({'status':'proposed','not_manufacturing_harness':True,'wires':wires},indent=2)+'\n')
    with (root/'external-wires.csv').open('w',newline='') as stream:
        keys=['wire_id','net_id','net_type','from_ref','from_pin','to_ref','to_pin','wire_gauge','cut_length_mm','routing_status']
        writer=csv.DictWriter(stream,fieldnames=keys,extrasaction='ignore');writer.writeheader();writer.writerows(wires)
    covered={w['net_id'] for w in wires}
    ids=[oid for item in request['bom'] for oid in item['object_ids']]
    assert len(ids)==len(set(ids)) and set(meshes)<=set(ids)
    assert covered=={n['net_id'] for n in electrical['nets']}
    (root/'exploded-view-verification.json').write_text(json.dumps({'native_device_solids':len(meshes),'bom_covers_device_solids':True,'additional_internal_layers':3,'net_count':len(covered),'external_display_branches':len(wires),'internal_pcb_wires':0,'physical_wire_routes_verified':False,'electrical_source_sha256':hashlib.sha256((root/request['electrical_file']).read_bytes()).hexdigest()},indent=2)+'\n')


if __name__=='__main__':
    render(Path(sys.argv[1]).resolve())
