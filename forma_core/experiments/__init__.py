"""Empirical verification independent of hardware and execution mechanisms."""
from .evaluation import compare_experiment, prepare_experiment
from .models import ExperimentPlan, ExperimentRequest, ExperimentRun

__all__ = ["compare_experiment", "prepare_experiment", "ExperimentPlan", "ExperimentRequest", "ExperimentRun"]
