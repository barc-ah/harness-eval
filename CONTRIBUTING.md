# Contributing

## Adding a harness

Most new harnesses need no Python. Add a block to `config/harnesses.yaml`
using the `shell` adapter:

```yaml
  - name: newtool
    adapter: shell
    command: "newtool run --model {model} -- {prompt}"
```

Write a real adapter class only when the harness exposes structured usage
worth parsing, or needs argv that a template cannot express. If you do, put it
in `adapters/impls.py`, register it in `REGISTRY`, and add a test asserting the
argv shape. Do not add scoring logic to an adapter; adapters run things and
report cost, `oracle.py` decides what passed.

## Changing scoring

Any change to `scoring.py` needs a test that would have failed before it. The
whole value of this tool is that its numbers are boring and reproducible, so
scoring changes get more scrutiny than features.

Two rules that are not negotiable:

- A missing measurement stays `None`. Never default a token count or a cost to
  zero; a report must be able to distinguish "not measured" from "free".
- No model judges another model's output. If you want a new signal, it has to
  come from an exit code or from the diff.

## Reporting a harness result

Include the config digest from the trial JSON, the harness versions from
`harness-eval doctor`, and the repeat count. A scorecard without those is
not reproducible and will be closed.
