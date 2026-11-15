# Membrane exchange preview

The GIF uses an isometric cutaway with shared projected ports and membrane interfaces.
Transverse dimensions are exaggerated for visibility. The model couples the two declared 20 × 1 × 0.2 mm channels through passive
solute exchange across their shared 10 µm porous PET membrane. Bulk flow stays
in each circuit; solute moves down the concentration gradient. The calculation
is a steady-state, 1D co-current plug-flow model, not CFD or biological validation.

## Explicit demonstration inputs

`examples/organ-on-chip/membrane-exchange-demo.json` declares:

- Effective solute permeability P = 0.005 mm/s (5 µm/s).
- Vascular inlet concentration Cv(0) = 1 mM.
- Epithelial inlet concentration Ce(0) = 0 mM.

These are illustrative assumptions for an unnamed passive solute. They are not
measured drug properties, Ewart experimental results, or inferred values from
pore size, membrane thickness, or porosity. Effective permeability absorbs
unresolved transport resistance. No cells, uptake, metabolism, adsorption,
reaction, or pressure-driven transmembrane flow are modeled. Concentrations
are treated as uniform across each channel cross-section; axial diffusion and
membrane storage are neglected.

## Equations and units

The local net flux is J = P(Cv − Ce). Positive flux means vascular → epithelial.
With Qv = 30/60 = 0.5 mm³/s, Qe = 10/60 = 1/6 mm³/s and width w = 1 mm:

```
Qv dCv/dx = −w P (Cv − Ce)
Qe dCe/dx = +w P (Cv − Ce)
Qv Cv + Qe Ce = constant
```

Concentrations in nmol/mm³ are numerically equal to mM. Thus J is nmol/mm²/s,
and Q C is nmol/s. The exact solution uses the flow-weighted equilibrium
Cbar = (Qv Cv(0) + Qe Ce(0))/(Qv + Qe) and
ΔC(x) = (Cv(0) − Ce(0)) exp[−Pw(1/Qv + 1/Qe)x].

```
Cv(x) = Cbar + Qe/(Qv + Qe) ΔC(x)
Ce(x) = Cbar − Qv/(Qv + Qe) ΔC(x)
```

The illustrative outlet concentrations are Cv(20) = 0.862332 mM and
Ce(20) = 0.413003 mM. Net transfer is 0.068834 nmol/s from vascular to epithelial.
Total solute flow is 0.5 nmol/s at both inlet and outlet. Unequal volumetric flows
explain why the concentration gain differs from the concentration loss.

## Animation mapping

- Blue/red tracers indicate bulk perfusion, at real-time mean Q/A speeds of
  0.833333/2.5 mm/s, respectively, with a common length scale.
- Gold channel shading follows the analytical steady concentration profile.
- Gold pulses cross the drawn membrane in the direction of calculated net flux.
  Their brightness/size indicates relative local flux; their timing and trajectory
  are symbolic. They are not individual molecules or molecular transit predictions.
- The visualization depicts an established steady state, not the response to
  switching an inlet on. Concentrations do not ramp up during this 12-second loop.

The generated `.exchange.json` records the parameters, 101-point profile,
outlet values, transfer and mass-balance residual. The renderer rejects unsupported
fixture topology rather than silently constructing an unrelated layout.

## Reproduce and check

```bash
python scripts/media/test_render_fluidic_demo.py
python scripts/media/test_membrane_exchange.py
python scripts/media/render_fluidic_demo.py --output docs/assets/organ-on-chip-flow.gif
```

Checks cover total mass conservation, concentration bounds, finite-difference
flux balances, zero permeability, equal concentrations, reversed gradient,
high-permeability equilibrium, invalid inputs, connected geometry, flow speeds
and animation phase. These verify implementation of the stated equations; they
do not validate permeability or performance against a physical liver chip.

Transport background:

- COMSOL [Thin Diffusion Barrier](https://doc.comsol.com/6.3/doc/com.comsol.help.porous/porous_ug_chemsptrans.08.24.html).
- COMSOL [Convection-Diffusion Equation](https://www.comsol.com/multiphysics/convection-diffusion-equation).

The plug-flow reduction and illustrative parameter choices above belong to this
demo, not a COMSOL calculation or a published liver-chip result.
