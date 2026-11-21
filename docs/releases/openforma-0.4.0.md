# OpenForma 0.4.0 — PyPI release

First release under the `openforma` PyPI package name.

## Install

```bash
pip install openforma==0.4.0
forma-core --help
forma-oss --help
fabricator --help
```

Requires Python 3.11 or newer. Python imports remain under `forma_core` and
`forma_cli`.

## Highlights

- Independent engineering-domain discovery, selection, authoring, composition,
  and validation, including chemical, process, and thermal domains.
- Resumable external-agent generation and semantic-to-geometry bindings.
- Executable machine packages, persistent agent task control, and native firmware
  simulation.
- Portable MuJoCo rover workcells and a two-rover preparation kitchen, with
  recorded concurrent delivery and parking evidence.

## Release artifacts

The attached wheel and source distribution match
[`openforma==0.4.0` on PyPI](https://pypi.org/project/openforma/0.4.0/).
They were built from commit `93f4f76` with the package-name, repository-URL, and
installation-documentation updates included in this release tag.

The `openforma-v0.4.0` tag distinguishes this package release from the earlier
`v0.4.0` release of `caid-forma-core`. The HTTP backend and web application are
run from the repository; the Python wheel contains the core and CLIs.

Validation: wheel and source distribution passed strict Twine checks; all three
CLI entry points passed clean-environment smoke checks; PyPI SHA-256 hashes
matched the local artifacts.
