#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
for repo in OpenForma OpenCAD OpenIndustries; do
  if [ ! -d "$repo" ]; then git clone "https://github.com/Mapped-Assembly/$repo.git" "$repo"; fi
done
for entry in 'OpenForma c22c86e' 'OpenCAD 9c0c6dc0b5794debdfb8c7507078bae3303dd8bd' 'OpenIndustries ab68bcf55d3e004c209eaf6a9426eb5cebafa13c'; do
  read -r repo commit <<< "$entry"
  if [ -n "$(git -C "$repo" status --porcelain)" ]; then
    if [ "$repo" != OpenForma ] || ! git -C OpenForma apply --reverse --check ../v3/openforma-v3.patch; then
      echo "Refusing to change a modified checkout: $repo" >&2
      exit 1
    fi
  else
    git -C "$repo" checkout "$commit"
  fi
done
if ! git -C OpenForma apply --reverse --check ../v3/openforma-v3.patch 2>/dev/null; then
  git -C OpenForma apply ../v3/openforma-v3.patch
fi
# Fresh-environment prerequisites:
# python -m pip install -e './OpenForma[robot-sim,process-render]' -e './OpenCAD/packages/opencad[occt,render]'
export MUJOCO_GL=egl
export PYTHONPATH="$PWD/OpenForma:$PWD/OpenCAD/packages/opencad/src"
python v3/tools/verify_composition.py v3/product-bundle.json
python v3/tools/compile_layer_geometry.py v3/layer-geometry.json
python v3/tools/inspect_imported_cad.py v3/layer-cad-review.json
cp v3/native-geometry-verification.json v3/layer-geometry-verification.json
python v3/tools/inspect_imported_cad.py v3/cad-review.json
python -m forma_core.control.mujoco_run v3/interaction.json v3/runtime-trace.json
python -m forma_core.control.mujoco_render v3/configuration.json v3/runtime-trace.json v3/views.json v3/smart-textile-v3-interaction.gif
python v3/tools/export_oi_input.py v3/configuration.json v3/runtime-trace.json v3/geometry.json v3/wearer-and-device-opencad.step v3/oi-composition-input.json --layer-geometry v3/layer-geometry.json
npm install --prefix .oi-tools --no-audit --no-fund esbuild@0.28.2 three ajv
node - <<'JS'
const esbuild=require('./.oi-tools/node_modules/esbuild');
esbuild.buildSync({entryPoints:['v3/compose_scene.ts'],bundle:true,platform:'node',format:'cjs',outfile:'v3/compose_scene.cjs',nodePaths:[process.cwd()+'/.oi-tools/node_modules']});
JS
node v3/compose_scene.cjs
python -m unittest OpenForma/tests/projects/test_layered_materials.py OpenForma/tests/projects/test_domain_irs.py OpenForma/tests/projects/test_domain_packages.py OpenForma/tests/projects/test_external_domain_providers.py OpenForma/tests/projects/test_thermal_ir.py
python -m unittest discover -s OpenForma/tests/control

# Annotated geometry review (requires matplotlib).
python v3/tools/render_exploded_bom.py v3/exploded-bom-request.json
python v3/tools/verify_issue_evidence.py v3/issue-evidence
python -m unittest OpenForma/tests/projects/test_pin_schematic.py OpenForma/tests/agents/test_system_architecture.py
# Optional PNG conversion, when PyMuPDF is installed:
# python -c "import fitz; d=fitz.open('v3/issue-evidence/electrical-interface.svg'); d[0].get_pixmap(matrix=fitz.Matrix(1.2,1.2)).save('v3/issue-evidence/electrical-interface.png')"
