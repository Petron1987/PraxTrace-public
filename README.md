# PraxTrace

Business evidence and continuous verification tools for AI agents.

Version `0.1.0a2` is an extraction preview distributed through [GitHub Releases](https://github.com/Petron1987/PraxTrace-public/releases). It is not published on PyPI.

Status: **0.1.0a2 extraction preview**, not a complete autonomous analyst. The package provides case registration, immutable deliverable snapshots, execution and evidence checks, source inventories, and optional pinned upstream tools. An AI agent interprets the evidence and writes the analysis. Automatic source monitoring and dependency-driven revalidation remain roadmap items.

## Install and use

Requires Python 3.10+; Git is needed only for source and upstream commands. Download the wheel from the [0.1.0a2 prerelease](https://github.com/Petron1987/PraxTrace-public/releases/tag/v0.1.0a2) and install it with `python -m pip install ./praxtrace-0.1.0a2-py3-none-any.whl`. This is a regular installation, not an editable install or a directory link.

```python
import praxtrace
result = praxtrace.check_record(record, root=workspace, phase="plan", require_brief=True)
```

```text
python -m praxtrace --help
python -m praxtrace guide
python -m praxtrace check execution.json --phase plan --require-case --require-brief
python -m praxtrace cases --help
python -m praxtrace ops --help
python examples/demo.py
python -m unittest discover -s tests
```

Run commands from your analysis workspace, or use the command-specific `--root` option. Mutable upstream state defaults to `.praxtrace/` in the working directory; set `PRAXTRACE_STATE_DIR` before process startup to override it. Package resources are read-only. Existing host projects can keep their own case/output locations; legacy report import currently defaults to `analysis/workspace/outputs` under the chosen workspace root.

Hosts can set `PRAXTRACE_SCAN_EXCLUDES` before import to a comma-separated list of directory names to exclude from case and artifact discovery, in addition to the built-in exclusions.

## Scope and boundaries

Bring your own agent, model and authorized data access. This package does not connect to production databases or send data to a model. Structural checks do not prove business truth, production deployment, causal impact, approval or delivery. Missing evidence must remain visible. Downloading an upstream checkout does not install or execute it.

English is the public product language. Multilingual user input and recall aliases are supported. Chinese maintenance documents belong to the private development repository and are excluded from the separately exported public repository and distribution packages.

A starter execution record is available as `execution-template.json` through `importlib.resources.files("praxtrace")`. See [Analysis methods](docs/methods.md).

See [Architecture](docs/architecture.md), [Workflow](docs/workflow.md), [Roadmap](docs/roadmap.md), and [Release checklist](docs/releasing.md).

Licensed under the MIT License; see LICENSE. Upstream tools retain their own licenses and are not bundled.
