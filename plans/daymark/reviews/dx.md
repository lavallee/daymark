# Developer experience review

2026-11-09. Mode: POLISH. This is agent review, not human acceptance.

Target hello world: under two minutes once Python is installed; zero credentials,
network services or notification providers. Core-only wheel must create a task,
replay it safely, complete it and export without importing optional dependencies.
Standalone HTTP requires explicit bearer configuration and binds loopback by default.
Library surface and HTTP schemas expose timing meanings and required versions.

Release acceptance: execute README examples from installed wheel outside checkout,
CLI help and hostile-input tests, clean sdist build, Python3.11–3.14 matrix. No
registry install claim until actual publication. Source install is the initial
supported path. Missing Store integration means this gate remains pending until
those checks actually run. Final release checklist replaces this pending status
with evidence; prose cannot substitute for a passing command.
