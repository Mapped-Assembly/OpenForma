"""Independent process-design IR contracts and conservative graph/balance checks.

The design models have no dependency on ChemicalIR, FluidicIR, MechanicalIR, or
OpenIndustries' operational runtime. Cross-domain links are separate artifacts. Optional execution orchestration lives
in processes.execution and reuses the empirical verification contracts.
"""
