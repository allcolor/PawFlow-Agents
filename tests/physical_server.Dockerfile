FROM pawflow-static-acceptance:local

USER root
# The isolated listener and stores use these core dependencies; no model downloads.
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    PyYAML cryptography jsonschema PyJWT httpx fastapi python-multipart tomli-w
