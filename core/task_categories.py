"""Task type → category mapping.

Canonical taxonomy used by the editor, color scheme, and any component
that needs to know which functional group a task belongs to.
"""

TASK_CATEGORIES = {
    # System
    "log": "System", "updateAttribute": "System", "replace_text": "System",
    "wait": "System", "fail": "System", "generateFlowFile": "System",
    "hashContent": "System", "listFiles": "System", "executeScript": "System",
    # IO
    "getFile": "IO", "putFile": "IO", "fetchHTTP": "IO", "listenHTTP": "IO",
    "getSFTP": "IO", "putSFTP": "IO", "listSFTP": "IO",
    "getFTP": "IO", "putFTP": "IO",
    "httpReceiver": "IO", "handleHTTPResponse": "IO", "validateHTTPAuth": "IO",
    "scraplingFetch": "IO",
    # Cloud
    "putS3": "Cloud", "getS3": "Cloud",
    "putGCS": "Cloud", "getGCS": "Cloud",
    "putAzureBlob": "Cloud", "getAzureBlob": "Cloud",
    # Data
    "transformJSON": "Data", "evaluateJSONPath": "Data", "extractText": "Data",
    "compressContent": "Data", "validateJSON": "Data", "convertCharset": "Data",
    "filterContent": "Data", "base64Encode": "Data", "countText": "Data",
    "convertCSVToJSON": "Data", "convertJSONToCSV": "Data",
    "executeSQL": "Data", "putSQL": "Data",
    "putCache": "Data", "getCache": "Data",
    "fetchDistributedMapCache": "Data", "putDistributedMapCache": "Data",
    "detectDuplicate": "Data", "attributesToJSON": "Data", "splitJSON": "Data",
    # Control
    "routeOnAttribute": "Control", "splitContent": "Control",
    "mergeContent": "Control", "duplicateContent": "Control",
    "funnel": "Control", "inputPort": "Control", "outputPort": "Control",
    "controlRate": "Control",
    # Messaging
    "publishKafka": "Messaging", "consumeKafka": "Messaging",
    "publishMQTT": "Messaging", "consumeMQTT": "Messaging",
    "sendEmail": "Messaging", "notifySlack": "Messaging",
    # Sync
    "waitForSignal": "Sync", "notify": "Sync",
    # Monitoring
    "reporting": "Monitoring",
    # AI
    "inferLLM": "AI", "agentLoop": "AI",
}
