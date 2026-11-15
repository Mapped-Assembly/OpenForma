"""Render a connected, assembled two-channel fluidic cross-section with Pillow.

Only the explicit two-circuit fixture is supported. Geometry is schematic;
main-channel tracer displacement is Q/A at real time and a common length scale.
Passive membrane exchange uses an explicit steady-state 1D model.
Biological response and CFD are not implied.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from membrane_exchange import MembraneExchange

ROOT = Path(__file__).resolve().parents[2]
W, H, SCALE, FPS, SECONDS = 1440, 1200, 2, 20, 12
BG, FG, MUTED = "#0c1522", "#edf3fa", "#a7b8cc"
BLUE, CORAL, GOLD = "#62d5ff", "#ff8994", "#ebce86"
LEFT, RIGHT = 510, 1110


@dataclass(frozen=True)
class Circuit:
    """Verified circuit data used for labels and main-channel kinematics."""

    name: str
    color: str
    top: int
    bottom: int
    length_mm: float
    width_mm: float
    height_mm: float
    flow_ul_min: float

    @property
    def speed_mm_s(self) -> float:
        """Return mean velocity; one microlitre equals one cubic millimetre."""
        return self.flow_ul_min / 60 / (self.width_mm * self.height_mm)

    @property
    def transit_s(self) -> float:
        """Return the main-channel residence time, excluding the chamber."""
        return self.length_mm / self.speed_mm_s

    @property
    def center(self) -> float:
        """Return the common centerline for tubing, chamber and channel."""
        return (self.top + self.bottom) / 2


def load_fixture(path: Path) -> tuple[list[Circuit], dict[str, Any]]:
    """Reject networks whose topology or dimensions cannot match this drawing."""
    system = json.loads(path.read_text(encoding="utf-8"))["fluidic_system"]
    channels = {c["id"]: c for c in system["channels"]}
    names = ("epithelial", "vascular")
    expected = {f"{n}_{suffix}" for n in names for suffix in ("supply", "feed", "channel")}
    if set(channels) != expected or system["valves"] or system["reservoirs"]:
        raise ValueError("Only the six-edge, two-circuit example is supported.")
    for field, suffix in (("inlets", "inlet"), ("outlets", "waste"),
                          ("pumps", "pump"), ("chambers", "chamber")):
        nodes = system[field]
        if len(nodes) != 2 or {n["id"] for n in nodes} != {f"{name}_{suffix}" for name in names}:
            raise ValueError(f"Unexpected {field} topology.")
        if any(n["circuit_id"] != n["id"].removesuffix(f"_{suffix}") for n in nodes):
            raise ValueError(f"Incorrect circuit ownership in {field}.")
    if len(system["membranes"]) != 1:
        raise ValueError("Exactly one membrane is required.")
    membrane = system["membranes"][0]
    if {membrane["side_a_id"], membrane["side_b_id"]} != {f"{n}_channel" for n in names}:
        raise ValueError("The membrane must contact both main channels.")
    circuits = []
    for name, color, top, bottom in (("epithelial", BLUE, 345, 411),
                                      ("vascular", CORAL, 415, 481)):
        route = ("inlet", "pump", "chamber", "waste")
        for suffix, source, target in zip(("supply", "feed", "channel"), route, route[1:]):
            edge = channels[f"{name}_{suffix}"]
            if (edge["source_id"], edge["target_id"]) != (f"{name}_{source}", f"{name}_{target}"):
                raise ValueError(f"Disconnected or reversed {name}_{suffix}.")
            for key in ("length_mm", "width_mm", "height_mm", "target_flow_rate_ul_min"):
                if not math.isfinite(edge[key]) or edge[key] <= 0:
                    raise ValueError(f"Invalid {key} on {edge['id']}.")
        main = channels[f"{name}_channel"]
        flow = main["target_flow_rate_ul_min"]
        if any(channels[f"{name}_{suffix}"]["target_flow_rate_ul_min"] != flow
               for suffix in ("supply", "feed")):
            raise ValueError("Supply, feed and main-channel flow must conserve volume.")
        pump = next(p for p in system["pumps"] if p["id"] == f"{name}_pump")
        if pump["target_flow_rate_ul_min"] != flow or flow > pump["max_flow_rate_ul_min"]:
            raise ValueError("Pump flow is inconsistent.")
        chamber = next(c for c in system["chambers"] if c["id"] == f"{name}_chamber")
        if chamber["inlet_ids"] != [f"{name}_feed"] or chamber["outlet_ids"] != [f"{name}_channel"]:
            raise ValueError("Chamber ports are inconsistent.")
        circuits.append(Circuit(name, color, top, bottom, main["length_mm"],
                                main["width_mm"], main["height_mm"], flow))
    if any((c.length_mm, c.width_mm, c.height_mm) != (20.0, 1.0, 0.2) for c in circuits):
        raise ValueError("This layout requires the fixture’s 20 × 1 × 0.2 mm channels.")
    if not math.isclose(membrane["thickness_mm"], 0.01):
        raise ValueError("This layout requires the fixture's 10 µm membrane.")
    # Tracers must return to the same phase after 12 seconds: no reset jump.
    for circuit in circuits:
        cycles = circuit.speed_mm_s * SECONDS / 2.5
        if not math.isclose(cycles, round(cycles)):
            raise ValueError("The requested flow does not produce a seamless 12-second loop.")
    return circuits, membrane


def exchange_model(circuits: list[Circuit]) -> MembraneExchange:
    """Bind explicit demonstration transport assumptions to the fixture flows."""
    values = json.loads((ROOT / "examples/organ-on-chip/membrane-exchange-demo.json").read_text())
    vascular = next(c for c in circuits if c.name == "vascular")
    epithelial = next(c for c in circuits if c.name == "epithelial")
    return MembraneExchange(vascular.flow_ul_min/60, epithelial.flow_ul_min/60,
                            vascular.width_mm, vascular.length_mm,
                            values["effective_permeability_mm_s"],
                            values["vascular_inlet_mM"], values["epithelial_inlet_mM"])


def blend(base: str, accent: str, fraction: float) -> str:
    """Mix a channel color with gold according to normalized solute concentration."""
    fraction = min(1.0, max(0.0, fraction)) * .6
    channels = [round(int(base[i:i+2], 16)*(1-fraction) + int(accent[i:i+2],16)*fraction)
                for i in (1,3,5)]
    return "#" + "".join(f"{v:02x}" for v in channels)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a consistent presentation font."""
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size * SCALE)


class Canvas:
    """Draw in logical pixels while retaining antialiasing at export."""

    def __init__(self) -> None:
        """Initialize a supersampled frame."""
        self.image = Image.new("RGB", (W * SCALE, H * SCALE), BG)
        self.draw = ImageDraw.Draw(self.image)

    def box(self, bounds: tuple[float, float, float, float], fill: str,
            outline: str | None = None, radius: int = 0) -> None:
        """Draw a rectangle with optional rounded corners."""
        self.draw.rounded_rectangle(tuple(round(v * SCALE) for v in bounds),
                                    radius=radius * SCALE, fill=fill, outline=outline, width=SCALE)

    def line(self, points: list[tuple[float, float]], color: str, width: int = 1) -> None:
        """Draw a continuous polyline."""
        self.draw.line([(round(x * SCALE), round(y * SCALE)) for x, y in points],
                       fill=color, width=width * SCALE, joint="curve")

    def text(self, x: float, y: float, value: str, size: int = 18,
             color: str = FG, bold: bool = False) -> None:
        """Draw one line of readable text."""
        self.draw.text((x * SCALE, y * SCALE), value, font=font(size, bold), fill=color)

    def polygon(self, points: list[tuple[float, float]], fill: str) -> None:
        """Draw a filled projected surface."""
        self.draw.polygon([(round(x*SCALE),round(y*SCALE)) for x,y in points],fill=fill)

    def dot(self, x: float, y: float, radius: float, color: str) -> None:
        """Draw a tracer confined to a channel."""
        self.draw.ellipse(tuple(round(v * SCALE) for v in (x-radius, y-radius, x+radius, y+radius)), fill=color)

    def arrow(self, x: float, y: float, color: str) -> None:
        """Draw a static rightward flow direction marker."""
        self.line([(x-8, y-6), (x, y), (x-8, y+6)], color, 2)


def tracer_positions(circuit: Circuit, time_s: float) -> list[float]:
    """Map regularly spaced passive tracers from millimetres to screen pixels."""
    spacing_mm = 2.5
    count = round(circuit.length_mm / spacing_mm)
    return [LEFT + ((n * spacing_mm + round(circuit.speed_mm_s * time_s, 12))
                    % circuit.length_mm) / circuit.length_mm * (RIGHT - LEFT)
            for n in range(count)]


def iso(x: float, y: float, z: float) -> tuple[float,float]:
    """Project orthogonal 3D axes at equal scale with true 30-degree isometry."""
    return 500+math.sqrt(3)/2*(x+y),490+.5*(x-y)-z


def prism(c: Canvas, x0: float, x1: float, y0: float, y1: float,
          z0: float, z1: float, top: str, front: str, side: str) -> None:
    """Draw a contiguous rectangular cutaway volume using one projection."""
    c.polygon([iso(x0,y0,z0),iso(x1,y0,z0),iso(x1,y0,z1),iso(x0,y0,z1)],front)
    c.polygon([iso(x1,y0,z0),iso(x1,y1,z0),iso(x1,y1,z1),iso(x1,y0,z1)],side)
    c.polygon([iso(x0,y0,z1),iso(x1,y0,z1),iso(x1,y1,z1),iso(x0,y1,z1)],top)


def render_frame(circuits: list[Circuit], membrane: dict[str, Any], time_s: float) -> Image.Image:
    """Render the same connected geometry at one real-time animation instant."""
    model = exchange_model(circuits)
    c = Canvas()
    c.text(54, 30, "MAPPED ASSEMBLY  /  FORMA-OSS", 16, GOLD, True)
    c.text(52, 70, "Liver-chip concept: membrane exchange", 38, FG, True)
    c.text(54, 133, "Fluid flows downstream. Solute crosses the membrane along its concentration gradient.", 23, MUTED)
    c.text(54, 205, "ISOMETRIC CUTAWAY", 16, GOLD, True)
    c.text(54, 233, "Steady-state passive-solute model • transverse dimensions exaggerated", 18, MUTED)
    # Tube endpoints use the same 3D projection as the exposed channel ports.
    for circuit in circuits:
        z = 103 if circuit.name == "epithelial" else 33
        y = 490-z
        inlet_port, outlet_port = iso(-90, 0, z), iso(600, 0, z)
        feed = [(150,y),(345,y),(375,y),inlet_port]
        waste = [outlet_port,(1190,outlet_port[1]),(1275,outlet_port[1])]
        for route in (feed,waste):
            c.line(route, "#40516a", 18)
            c.line(route, circuit.color, 8)
        c.box((110,y-19,150,y+19), "#1a2b3f", circuit.color,5)
        c.box((245,y-20,335,y+20), "#1a2b3f", circuit.color,6)
        c.text(258,y-11,"PUMP",15,circuit.color,True)
        c.box((1275,outlet_port[1]-22,1365,outlet_port[1]+22),"#1a2b3f",circuit.color,5)
        c.text(1286,outlet_port[1]-11,"WASTE",15,circuit.color,True)
        c.arrow(205,y,FG)
        c.arrow(1215,outlet_port[1],FG)
        concentration = model.epithelial_inlet_mm if circuit.name == "epithelial" else model.vascular_inlet_mm
        c.text(111,y-48,f"{concentration:g} mM",14,GOLD)
    # Draw back-to-front: lower housing, vascular lumen, membrane, upper lumen.
    prism(c,0,600,0,120,-26,0,"#34465c","#243248","#40546b")
    for name,z0,z1,base,color in (("vascular",0,66,"#472735",CORAL),
                                  ("epithelial",70,136,"#123a4e",BLUE)):
        if name == "epithelial":
            prism(c,0,600,0,120,66,70,GOLD,"#bfa466","#9b8145")
            for x in range(12,600,18):
                c.line([iso(x,0,66),iso(x,0,70)],"#8d7546")
        # The enlarged distribution chamber is contiguous with its main lumen.
        inlet = model.vascular_inlet_mm if name == "vascular" else model.epithelial_inlet_mm
        prism(c,-90,0,0,120,z0,z1,blend(base,GOLD,inlet),blend(base,GOLD,inlet),color)
        for x in range(0,600,4):
            cv,ce=model.concentrations(x/30)
            shade=blend(base,GOLD,cv if name == "vascular" else ce)
            # Both the cut face and open top show the same solved concentration.
            c.polygon([iso(x,0,z0),iso(x+4,0,z0),iso(x+4,0,z1),iso(x,0,z1)],shade)
            c.polygon([iso(x,0,z1),iso(x+4,0,z1),iso(x+4,120,z1),iso(x,120,z1)],shade)
        c.polygon([iso(600,0,z0),iso(600,120,z0),iso(600,120,z1),iso(600,0,z1)],blend(base,GOLD,model.concentrations(20)[0 if name == "vascular" else 1]))
        for z in (z0,z1):
            c.line([iso(-90,0,z),iso(600,0,z)],color,2)
        # Exposed back housing rail indicates the removed cover/cut wall.
        prism(c,0,600,110,120,z1,z1+18,"#40546b","#27394e","#536881")
        circuit=next(item for item in circuits if item.name == name)
        for screen_x in tracer_positions(circuit,time_s):
            along=screen_x-LEFT
            if 5<=along<=595:
                px,py=iso(along,0,(z0+z1)/2)
                c.dot(px,py,4,color)
                c.dot(px,py,1.5,FG)
        lx,ly=iso(25,0,z1-13 if name == "epithelial" else 13)
        c.text(lx,ly,name.upper(),14,color,True)
    # Render outlet fittings over the exposed end face, with tubes leaving along
    # the projected channel axis before bending toward their collection vessels.
    for circuit in circuits:
        z = 103 if circuit.name == "epithelial" else 33
        port, exit_point = iso(600,60,z), iso(660,60,z)
        route = [port,exit_point,(1190,exit_point[1]),(1275,exit_point[1])]
        c.line(route,"#40516a",18)
        c.line(route,circuit.color,8)
        c.dot(*port,9,"#0c1522")
        c.dot(*port,5,circuit.color)
        c.arrow(1215,exit_point[1],FG)
    # Preserve the membrane-normal flux arrows and their solved strength.
    for i,x in enumerate(range(40,590,50)):
        flux=model.flux_nmol_mm2_s(x/30)
        maximum=abs(model.flux_nmol_mm2_s(0))
        if maximum>0 and abs(flux)>1e-12:
            strength=abs(flux)/maximum
            shade=blend("#53442c","#fff0b1",strength)
            phase=(round(time_s/1.2,10)+i/12)%1
            z=44+48*phase if flux>0 else 92-48*phase
            c.line([iso(x,0,44),iso(x,0,92)],shade)
            tip=iso(x,0,92 if flux>0 else 44)
            tail_y=tip[1]+6 if flux>0 else tip[1]-6
            c.line([(tip[0]-4,tail_y),tip,(tip[0]+4,tail_y)],GOLD,2)
            c.dot(*iso(x,0,z),2.5+strength,GOLD)
    c.text(108,530,"Inlets",16,MUTED)
    c.text(243,530,"Pumps",16,MUTED)
    c.text(375,590,"Distribution",16,MUTED)
    c.text(375,613,"chambers",16,MUTED)
    c.text(1260,803,"Separate waste",16,MUTED)
    c.text(1050,310,"Cover cut away",16,MUTED)
    c.text(530,864,f"{membrane['material'].upper()} MEMBRANE  ·  10 µm",17,GOLD,True)
    c.text(530,893,"Gold shading: solute concentration. Gold pulses: net solute exchange.",17,MUTED)
    c.text(54,864,"J = P × (Cv − Ce)",22,GOLD,True)
    c.text(54,900,f"Assumed P = {model.permeability_mm_s*1000:g} µm/s",18,MUTED)
    for circuit, x in zip(circuits, (54, 735)):
        c.box((x, 959, x+651, 1100), "#142337", "#2b3c51", 12)
        c.text(x+22, 976, circuit.name.title(), 22, circuit.color, True)
        c.text(x+22, 1018, f"{circuit.flow_ul_min:g} µL/min", 28, FG, True)
        c.text(x+230, 1021, f"{circuit.speed_mm_s:.2f} mm/s", 22, FG)
        c.text(x+446, 1021, f"{circuit.transit_s:g} s transit", 22, FG)
        cv, ce = model.concentrations(model.length_mm)
        outlet = ce if circuit.name == "epithelial" else cv
        c.text(x+22, 1061, f"Solute outlet: {outlet:.3f} mM", 19, GOLD)
    c.text(54, 1116, "20 × 1 × 0.2 mm channels  ·  Solute mass conserved  ·  Fluid tracers: real time; exchange pulses: symbolic", 17, MUTED)
    c.text(54, 1152, "Illustrative permeability and concentrations. 1D transport model; no cell response, CFD, or toxicity prediction.", 16, MUTED)
    return c.image.resize((W, H), Image.Resampling.LANCZOS)


def render(project_path: Path, output: Path) -> None:
    """Write a seamless GIF and a matching still from the verified fixture."""
    circuits, membrane = load_fixture(project_path)
    first = render_frame(circuits, membrane, 0)
    palette = first.quantize(colors=192)
    frames = [render_frame(circuits, membrane, n / FPS).quantize(palette=palette, dither=Image.Dither.NONE)
              for n in range(FPS * SECONDS)]
    output.parent.mkdir(parents=True, exist_ok=True)
    first.save(output.with_suffix(".png"))
    output.with_suffix(".exchange.json").write_text(json.dumps(exchange_model(circuits).report(), indent=2)+"\n")
    frames[0].save(output, save_all=True, append_images=frames[1:], loop=0,
                   duration=1000 // FPS, optimize=True, disposal=1)
    print(f"{output}: {len(frames)} frames, {SECONDS} seconds, {W}x{H}")


def main() -> None:
    """Parse the fixture and output paths for command-line reproduction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT / "examples/organ-on-chip/two-channel.json")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render(args.project, args.output)


if __name__ == "__main__":
    main()
