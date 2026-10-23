# testkit: exempt fixture program for the private Serena stdio protocol
"""Synthetic REPL protocol peer. Real Serena/PHP evidence uses the live smoke."""
import ast
import json
import sys

namespaces = {}
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fixture-serena", "version": "fixture"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "serena_repl", "inputSchema": {
            "type": "object", "properties": {"session": {"type": "string"}, "code": {"type": "string"}}}}]}
    elif method == "tools/call":
        args = request["params"]["arguments"]
        namespace = namespaces.setdefault(args["session"], {})
        try:
            module = ast.parse(args["code"])
            final = module.body.pop() if module.body and isinstance(module.body[-1], ast.Expr) else None
            exec(compile(module, "<fixture-cell>", "exec"), namespace)  # noqa: S102 - synthetic REPL peer
            text = str(eval(compile(ast.Expression(final.value), "<fixture-cell>", "eval"), namespace)) if final else "None"  # noqa: S307 - synthetic REPL peer
        except Exception as error:
            text = f"{type(error).__name__}: {error}\n  line 1: fixture cell"
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
