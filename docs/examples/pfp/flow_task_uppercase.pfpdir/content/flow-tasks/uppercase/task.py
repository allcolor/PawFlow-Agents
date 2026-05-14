from pathlib import Path
from pawflow import pfp

payload = pfp.payload
flowfile = payload.get("flowfile", {})
content = Path(flowfile["content_path"]).read_text()
attributes = dict(flowfile.get("attributes") or {})
attributes["pfp.task"] = "exampleUppercase"
pfp.result(flowfiles=[pfp.flowfile(content.upper(), attributes)])
