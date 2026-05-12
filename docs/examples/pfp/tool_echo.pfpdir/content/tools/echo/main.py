from pawflow import pfp

text = str(pfp.payload.get("arguments", {}).get("text", ""))
pfp.result(text)
