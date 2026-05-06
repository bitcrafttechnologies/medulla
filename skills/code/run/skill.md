# skill: code.run
version: 1.0.0
tier: recommended
author: Bitcraft Technologies
---

## Intent

Executes sandboxed Python or JavaScript (Node.js) code and returns structured output. This is the generative execution skill — where Echo moves from reasoning about code to actually running it. Unlike `shell.exec`, which runs arbitrary commands, `code.run` is scoped entirely to code evaluation with explicit I/O capture and no filesystem side effects by default.

Echo uses this skill to verify generated code before delivering it, run quick computations, process data inline, prototype logic, and demonstrate results rather than just describing them.

---

## Inputs

| Field        | Type   | Required | Description                                                                |
|-------------|--------|----------|----------------------------------------------------------------------------|
| `language`   | enum   | yes      | `python` or `javascript`                                                   |
| `code`       | string | yes      | Source code to execute                                                     |
| `stdin`      | string | no       | Data piped to the process stdin. Default: empty                            |
| `timeout_sec`| int    | no       | Execution timeout. Default: `15`. Max: `60`                                |
| `allow_fs`   | bool   | no       | Allow file reads/writes during execution. Default: `false`                 |
| `allow_net`  | bool   | no       | Allow network access during execution. Default: `false`                    |
| `packages`   | string[] | no     | Additional packages to ensure are available before running (pip/npm)       |
| `env`        | object | no       | Environment variables available to the process                             |
| `confirm`    | bool   | no       | Require user approval before running. Default: `true` for any network/fs access |

---

## Outputs

```json
{
  "language": "python",
  "exit_code": 0,
  "stdout": "Hello from Echo\n42\n",
  "stderr": "",
  "elapsed_sec": 0.04,
  "timed_out": false,
  "truncated": false
}
```

On error:
```json
{
  "language": "python",
  "exit_code": 1,
  "stdout": "",
  "stderr": "NameError: name 'x' is not defined\n  File \"<string>\", line 3",
  "elapsed_sec": 0.01,
  "timed_out": false
}
```

On timeout:
```json
{
  "language": "python",
  "exit_code": -1,
  "stdout": "",
  "stderr": "",
  "elapsed_sec": 15.0,
  "timed_out": true
}
```

---

## Sandbox Model

By default, `code.run` executes with:

- **No filesystem access** — code cannot open, read, or write files (enforced via restricted builtins for Python; no `fs` module for JS)
- **No network access** — outbound connections are blocked
- **No subprocess spawning** — `subprocess`, `os.system`, `child_process` are blocked
- **Memory limit** — `256 MB` soft limit. Processes exceeding this are killed.
- **Output cap** — stdout/stderr each capped at `64 KB`. Excess is truncated.

When `allow_fs: true` or `allow_net: true` are set, the confirmation requirement is automatically enforced regardless of the `confirm` field value. Echo must surface this to the user before proceeding.

### Python Sandbox

The Python sandbox removes dangerous builtins before execution:
```python
BLOCKED = {"__import__", "open", "eval", "exec", "compile",
           "breakpoint", "input", "__loader__", "__spec__"}
```

`import` is allowed but intercepted — `subprocess`, `socket`, `os.system`, `shutil.rmtree` are blocked at import time.

### JavaScript Sandbox

Node.js is invoked with `--disallow-code-generation-from-strings` and a module wrapper that removes access to `require('fs')`, `require('child_process')`, and `require('net')` unless `allow_fs`/`allow_net` are enabled.

---

## Safety Constraints

- Code that imports blocked modules produces a clear error rather than silently failing.
- Infinite loops are caught by the timeout — no process can survive past `timeout_sec`.
- Echo should always review generated code output before presenting it to the user as a final answer. If `exit_code != 0`, Echo should diagnose the error rather than just passing it through.
- `packages` installs are performed once and cached in `.echospace/skill/.pkg_cache/`. No package installation happens at execution time without prior approval.

---

## Dependencies

- Python: built-in (`python3` on PATH)
- JavaScript: `node` on PATH
- Package install: `pip` (Python), `npm` (JS)

---

## Medulla Integration

Execution receipts are emitted to Medulla after each run:

```json
{
  "jsonrpc": "2.0",
  "method": "receipt.code_run",
  "params": {
    "language": "python",
    "exit_code": 0,
    "elapsed_sec": 0.04,
    "stdout_bytes": 18,
    "sandbox": { "allow_fs": false, "allow_net": false }
  }
}
```

---

## Invocation Examples

**Quick computation:**
```json
{
  "language": "python",
  "code": "import math\nprint(math.factorial(20))",
  "timeout_sec": 5
}
```

**Data processing:**
```json
{
  "language": "python",
  "code": "import json, sys\ndata = json.load(sys.stdin)\nprint(len(data))",
  "stdin": "[1, 2, 3, 4, 5]"
}
```

**JavaScript snippet:**
```json
{
  "language": "javascript",
  "code": "const nums = [1,2,3,4,5];\nconsole.log(nums.reduce((a,b) => a+b, 0));"
}
```

**File-producing task (with fs enabled):**
```json
{
  "language": "python",
  "code": "with open('/tmp/echo_out.txt', 'w') as f:\n    f.write('done')",
  "allow_fs": true,
  "confirm": true
}
```
