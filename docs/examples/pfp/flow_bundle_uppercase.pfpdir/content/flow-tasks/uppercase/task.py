import base64
from pawflow import pfp

payload = pfp.payload
flowfile = payload.get("flowfile", {})
content = base64.b64decode(flowfile.get("content_b64", "")).decode("utf-8")
attributes = dict(flowfile.get("attributes") or {})
attributes["pfp.task"] = "exampleUppercase"
pfp.result(flowfiles=[pfp.flowfile(content.upper(), attributes)])
