# Forma Project Object Examples

These examples create real `FormaProjectObject` instances from generated `HardwareIntermediateRepresentation`.
The hardcoded scripts run Ollama, Runpod, and Baseten GLM by default. Hugging Face Qwen is wired in and can be enabled after setting a token.

Hardcoded sync example:

```bash
./examples/simple_sync_project_objects.py
```

Hardcoded async example:

```bash
./examples/simple_async_project_objects.py
```

Hardcoded async prompt comparison for Baseten GLM and OpenAI:

```bash
./examples/async_glm_openai_prompt.py
```

The original configurable examples are still here if you want them later.

Configurable sync:

```bash
./examples/sync_project_objects.py
```

Configurable async:

```bash
./examples/async_project_objects.py
```

Outputs are written to `examples/results/`:

- `*.project-object.json`
- `*.hardware-ir.json`
- `*-summary.json`

The examples run in strict test mode. Provider failures raise and are recorded as
failed jobs; they do not fall back to simulation.

## Forma Core samples

Focused Forma Core examples live in `examples/forma_core/`:

```bash
python examples/forma_core/sample.py
python examples/forma_core/sample1.py
python examples/forma_core/sample2.py
python examples/forma_core/sample3.py
```

- `sample.py` generates a simulated project from a frozen design brief.
- `sample1.py` generates a project with a local Ollama model.
- `sample2.py` combines Ollama generation with Firecrawl web research.
- `sample3.py` combines Vertex AI generation with Firecrawl web research.

For `sample3.py`, authenticate with Google Cloud Application Default Credentials
and configure the Vertex AI project plus Firecrawl:

```bash
gcloud auth application-default login
GOOGLE_CLOUD_PROJECT=your-project-id \
GOOGLE_CLOUD_LOCATION=global \
VERTEX_AI_MODEL=gemini-3.5-flash \
FIRECRAWL_API_KEY=your-firecrawl-key \
python examples/forma_core/sample3.py
```

Useful overrides:

```bash
./examples/sync_project_objects.py --only ollama --ollama-model qwen3:8b
./examples/sync_project_objects.py --only runpod --timeout-seconds 1200
./examples/sync_project_objects.py --only baseten --baseten-model zai-org/GLM-5.2
./examples/sync_project_objects.py --only huggingface --huggingface-model Qwen/Qwen2.5-Coder-3B-Instruct:nscale
```

Baseten GLM uses `zai-org/GLM-5.2` against `https://inference.baseten.co/v1`.
Hugging Face uses `Qwen/Qwen2.5-Coder-3B-Instruct:nscale` against `https://router.huggingface.co/v1`;
set `HF_TOKEN`, `HUGGINGFACE_API_KEY`, or `HUGGINGFACE_HUB_TOKEN` first.

Raw Runpod Parti behavior probe:

```bash
./examples/caid-technologies/parti-base
```

It writes raw chat/completions responses and seed classifications to
`examples/results/*-caid-technologies-parti-base-behavior.json`.

### Drive the captive hinge with MuJoCo

From the repository root, run the following in PowerShell or a shell.

```sh
python -m pip install -e ".[mujoco]"
forma-core mujoco examples/print_in_place_hinge.json
```

The session prints its joint ID, moving part, input unit and travel limits. Type
these commands one at a time. Each response reports simulation time, target,
measured position and velocity. Time advances by 2 ms per requested step.

```text
start
set 1
step 1
step 99
set 0.25
step 100
pause
reset
close
```

The same session is available from Python as
`forma_core.mechanics.mujoco_runtime.MuJoCoRuntime(project_dict)`.
This adapter supports one resolved, fixed-base revolute or prismatic joint.
It uses component placement boxes at 1000 kg/m³, zero gravity, disabled
contacts, and a virtual position servo. Hinge inputs are radians and slider
inputs are meters. CAD preview samples are ignored. The simulation exercises
controller behavior; material, actuator and contact calibration are outside
this model.
