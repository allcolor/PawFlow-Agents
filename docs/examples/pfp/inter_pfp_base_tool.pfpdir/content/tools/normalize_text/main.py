from pawflow import pfp

args = pfp.payload.get("arguments", {})
text = " ".join(str(args.get("text", "")).split())
if args.get("uppercase"):
    text = text.upper()
pfp.result(text)
