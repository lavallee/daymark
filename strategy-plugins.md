# Add a strategy to Daymark

Suppose a print coordinator wants a short list of ready tasks that still need
an estimate. A strategy can name those tasks during a review. The task owner
keeps the decision about whether to add an estimate and what it should be.

## Write the read

Create `src/print_method/__init__.py` with this strategy:

```python
from daymark.strategies import Note, Param, Strategy


def review(view, advice, params):
    missing = [item["id"] for item in view.ready if item["estimate_minutes"] is None]
    if missing:
        advice.notes.append(
            Note("estimate-review", "Ready tasks without estimates.", missing[:params["sample"]])
        )


strategy = Strategy(
    name="estimate-review",
    kind="hygiene",
    summary="Name ready tasks without a recorded estimate.",
    evidence="convention",
    apply=review,
    params={"sample": Param(3, "How many task IDs to show", 1, 10)},
)
```

`view.ready` contains the caller's eligible tasks in their ordinary order.
`advice.picks` reflects preceding stages in the selected profile. Choose the
one that answers the question your strategy asks. Return suggestions or notes
through `advice`. A person or authorized agent handles any resulting write.

The current development API also accepts `reads=()` for a strategy that needs
only the ready candidates and clock. Leaving the keyword out keeps this
example compatible with the v0.5.0 strategy signature.

For the development API, an optional `evidence_note` can explain which
parts of the practice a source evaluated and which are the author's design
choices. A reader sees it when inspecting that strategy. For example,
`daymark strategies first-step` explains the difference between a whole
program assessed in a trial and a particular checklist display.

## Declare the entry point

Use this `pyproject.toml` at the package root. Its entry-point name matches
`Strategy.name`, and the value points at the Python object above.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "daymark-example-print-method"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["daymark-core>=0.5,<1"]

[project.entry-points."daymark.strategies"]
estimate-review = "print_method:strategy"

[tool.hatch.build.targets.wheel]
packages = ["src/print_method"]
```

Build the wheel with `uv build --wheel`. Install it into the Python
environment that runs the Daymark CLI or MCP server. For a development
checkout with a virtual environment, run `uv pip install --python
.venv/bin/python PATH_TO_WHEEL` from the Daymark checkout. Restart a
long-running MCP server after changing installed packages.

## Try it once

```sh
daymark strategies estimate-review
daymark advise --profile plain --set estimate-review.sample=2
```

Choose a distinct entry-point name. Built-ins take precedence and two external
packages offering an identical unused name make selection ambiguous. A fresh
process lists installed entry-point names through package metadata. Previewing the strategy imports its Python module. Once a person
asks for the same review again, a named configuration can retain it:

```sh
daymark profile plain --set estimate-review.sample=2 --save-as print-review
daymark advise --profile print-review
```

A saved configuration names a strategy and its parameter values. If the
package disappears or its module cannot load, advice reports the unavailable
stage. Editing other stages, adopting the named profile or saving a copy keeps
its position and parameters. A host with the package can use them again.
Changing that stage's parameters requires a working installation. Use
`--drop STRATEGY` to remove the stage deliberately.

To move the whole configuration to another database, export a named copy with
`daymark profile --saved NAME --json`. This includes locally unavailable
stages. The active profile display lists their names under `skipped` but has
no parameter values for them. Importing that display requires explicitly
dropping any stages known only by name.

When a package retires a parameter, the active profile shows the saved value
under `unavailable_parameters` alongside the effective settings from the
installed version. Advice names the affected parameter. Editing a different
stage keeps that value in storage, so a compatible installation can recover it.
To migrate an old `estimate-review.limit` value of 2 to a replacement
`sample` parameter, preview the edit and then save it if requested:

```sh
daymark advise --drop estimate-review.limit --set estimate-review.sample=2
daymark profile --drop estimate-review.limit --set estimate-review.sample=2
```

Use the replacement name and value confirmed by the person. Daymark cannot
infer that two differently named parameters express the same preference.

Install packages you trust to run on the host. A strategy executes ordinary
Python in Daymark's process. The read-only MCP option controls Daymark's tool
surface and database connection. Operating-system isolation for an extension
is a separate host responsibility.
