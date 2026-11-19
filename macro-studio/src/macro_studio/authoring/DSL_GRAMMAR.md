# Macro Studio DSL v1.4 grammar

Source is parsed as Python text, checked, and compiled to the common IR. The Runner executes that IR.

## Declarations

Use `async def` with exactly one of `@on_start()`, `@on_hotkey("CTRL+F6")`, `@on_interval(ms=1000)` or `@workflow("name")`.
Event functions have no parameters. Workflow parameters require `int`, `str` or `bool` annotations. Return values are unsupported.
Event decorators accept `priority` and `reentrancy`. The latter is one of `ignore_if_running`, `restart`, `queue`, `parallel`, `override`.
Function names, workflow names and event hotkeys must be unique. Workflow calls must be acyclic.

## Statements and expressions

Supported statements are `await` of registered APIs, scalar assignment, augmented assignment, `if/else`, `for i in range(n)`, a `while` with a condition, `break`, `continue` and `pass`.
The loop count is an integer from 0 through 1,000,000. `while True` is rejected. Conditional loops have a runtime iteration bound.
Use `await macro.sleep(ms=...)` within polling loops to allow a useful delay. STOP can cancel scheduled work.
Only `range(n)` is supported. Other built-in calls are unavailable.

Scalar values are numbers, strings and booleans. Operators are `+`, `-`, `*`, `/`, `==`, `!=`, `<`, `<=`, `>`, `>=`, `and`, `or`, `not` and unary minus.
Chained comparisons, containers, comprehensions and arbitrary object attributes are unsupported.
Integer API parameters require integer values. Division produces a number and does not make an integer duration argument valid.
Local scalar variables are isolated for each workflow invocation. `global name` can refer to an existing public project variable.
Project context exports variable names and types. Variable initial values are private and remain in the project.

## Awaited results

Assign `vision.find` and `vision.wait_for` results to a local name. Read only the result fields listed in the API reference.
Compare `result.status` to the string `"FOUND"`, `"NOT_FOUND"` or `"UNKNOWN"`.
Use coordinates for input only on the `FOUND` branch. The runtime checks freshness and can revalidate before clicking.
The result of `actions.run` and `workflow.call` cannot be assigned to a variable.

## Rejected syntax

`import`, direct function execution, `exec`, `eval`, `getattr`, `open`, dunder access, decorators outside the allowlist, `class`, `lambda`, `try/except`, recursion and unbounded busy loops are rejected.
Use result status branches for recognition errors. Ordinary Python modules and third-party libraries cannot be imported.
The source limit is 65,536 characters and 4,096 AST nodes. API parameter bounds are in `api-manifest.json`.

## Block conversion

Only the shared Blockly/Python subset can be converted. Unsupported nodes cause a diagnostic and preserve the original source.
Blockly hotkeys require at least one modifier. Use a combination such as `CTRL+F6` when round-trip conversion is needed.
Declared actions expand to standard IR and may appear as a one-iteration loop with ordinary input blocks after conversion.
Original Python drafts remain available through the Python menu.
