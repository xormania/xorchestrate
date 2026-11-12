"""Restricted interpretation of workflow expressions for event-routing scenarios.

This evaluates the checked-in expression, independently of the routing code.
Missing properties and short-circuit values follow the GitHub expression rules
used by these scenarios. Unsupported syntax fails rather than guessing.
"""

import ast
import re


def expression(value, context, *, cancelled=False):
    if not isinstance(value, str):
        return value
    if not value.startswith("${{"):
        if "${{" in value:
            return re.sub(r"\$\{\{.*?\}\}", lambda match: str(expression(match[0], context, cancelled=cancelled)), value)
        return value
    source = value.removeprefix("${{").removesuffix("}}").strip()
    source = re.sub(r"!(?!=)", " not ", source).replace("&&", " and ").replace("||", " or ").strip()

    def visit(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return {"true": True, "false": False, "null": None}.get(node.id, context.get(node.id))
        if isinstance(node, ast.Attribute):
            value = visit(node.value)
            return value.get(node.attr) if isinstance(value, dict) else None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not visit(node.operand)
        if isinstance(node, ast.BoolOp):
            result = visit(node.values[0])
            for operand in node.values[1:]:
                if isinstance(node.op, ast.And) and not result or isinstance(node.op, ast.Or) and result:
                    break
                result = visit(operand)
            return result
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = visit(node.left), visit(node.comparators[0])
            # These workflow predicates use booleans or strings, not numeric coercion.
            if isinstance(left, str) and isinstance(right, str):
                left, right = left.casefold(), right.casefold()
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            arguments = [visit(argument) for argument in node.args]
            name = node.func.id
            if name == "always" and not arguments:
                return True
            if name == "cancelled" and not arguments:
                return cancelled
            if name == "success" and not arguments:
                return all(item.get("result") == "success" for item in context.get("needs", {}).values())
            if name == "format" and arguments:
                return arguments[0].format(*arguments[1:])
            if name == "contains" and len(arguments) == 2:
                return arguments[1] in arguments[0]
        raise AssertionError("Unsupported workflow expression: " + ast.dump(node))

    return visit(ast.parse(source, mode="eval").body)


def pr_context(action, changes=None, *, head="a" * 40, base="b" * 40, number=17):
    return {"github": {"event_name": "pull_request", "repository_id": 77, "ref": f"refs/pull/{number}/merge",
        "event": {"action": action, "changes": changes or {}, "pull_request": {
            "number": number, "head": {"sha": head}, "base": {"sha": base}}}},
        "needs": {"paths": {"result": "success", "outputs": {"code": "true", "reuse": "false"}},
                  "changes": {"result": "success"}, "application-tests": {"result": "success"}}}
