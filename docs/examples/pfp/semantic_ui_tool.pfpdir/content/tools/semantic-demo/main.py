from pawflow import pfp

arguments = pfp.payload.get("arguments", {})
operation = str(arguments.get("operation") or "")
package = "examples.semantic-ui"
node = str(arguments.get("node") or f"{package}:demo.status")

if operation == "list":
    result = pfp.browser.semantic.list(package)
elif operation == "get":
    result = pfp.browser.semantic.get(package, node)
elif operation == "invoke":
    result = pfp.browser.semantic.invoke(
        package,
        node,
        str(arguments.get("action") or ""),
        arguments.get("arguments") or {},
    )
else:
    raise ValueError(f"unsupported semantic operation: {operation}")

pfp.result(result)
