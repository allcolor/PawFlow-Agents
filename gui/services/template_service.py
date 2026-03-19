# Template Service

"""
Service pour la gestion des modeles de flux (flow templates).
Full template library with categories, metadata, search, import/export.
"""

import copy
import json
import os
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Template categories
TEMPLATE_CATEGORIES = [
    "ETL",
    "Monitoring",
    "Communication",
    "Data Processing",
    "Integration",
    "Custom",
]

CATEGORY_ICONS = {
    "ETL": "🔄",
    "Monitoring": "📊",
    "Communication": "📨",
    "Data Processing": "🧮",
    "Integration": "🔗",
    "Custom": "📦",
}

# Difficulty levels
DIFFICULTY_LEVELS = ["beginner", "intermediate", "advanced"]


class TemplateService:
    """Service de gestion des modeles de flux."""

    def __init__(self):
        self.templates_dir = "templates"
        os.makedirs(self.templates_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Listing & retrieval
    # ------------------------------------------------------------------

    def list_templates(self, category: Optional[str] = None) -> List[Dict]:
        """Lister les templates disponibles (fichiers + builtin).

        Args:
            category: Optional category filter.
        """
        templates = []

        # Builtin templates
        for t in self.get_builtin_templates():
            entry = self._template_summary(t, builtin=True)
            templates.append(entry)

        # File-based templates
        templates_path = Path(self.templates_dir)
        for filepath in templates_path.glob("*.json"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    entry = self._template_summary(data, builtin=False)
                    templates.append(entry)
            except Exception as e:
                logger.error(f"Erreur lecture template {filepath}: {e}")

        if category:
            templates = [t for t in templates if t.get("category") == category]

        return templates

    def load_template(self, template_id: str) -> Dict:
        """Charger un template par son ID."""
        # Check builtin first
        for t in self.get_builtin_templates():
            if t["id"] == template_id:
                result = copy.deepcopy(t)
                result["id"] = f"flow_{uuid.uuid4().hex[:8]}"
                return result

        # File-based
        filepath = Path(self.templates_dir) / f"{template_id}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Template non trouve: {template_id}")

        with open(filepath, "r", encoding="utf-8") as f:
            result = json.load(f)
            result["id"] = f"flow_{uuid.uuid4().hex[:8]}"
            return result

    def get_template(self, template_id: str) -> Optional[Dict]:
        """Get a template by ID without generating a new flow ID.

        Returns the raw template dict or None if not found.
        """
        for t in self.get_builtin_templates():
            if t["id"] == template_id:
                return copy.deepcopy(t)

        filepath = Path(self.templates_dir) / f"{template_id}.json"
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)

        return None

    # ------------------------------------------------------------------
    # Search & filter
    # ------------------------------------------------------------------

    def search_templates(self, query: str) -> List[Dict]:
        """Search templates by name, description, and tags.

        Args:
            query: Search string (case-insensitive).
        """
        query_lower = query.lower()
        results = []

        all_templates = self.list_templates()
        for t in all_templates:
            name_match = query_lower in t.get("name", "").lower()
            desc_match = query_lower in t.get("description", "").lower()
            tag_match = any(query_lower in tag.lower() for tag in t.get("tags", []))
            if name_match or desc_match or tag_match:
                results.append(t)

        return results

    def get_templates_by_category(self, category: str) -> List[Dict]:
        """Get all templates in a given category."""
        return self.list_templates(category=category)

    # ------------------------------------------------------------------
    # Save / Import / Export
    # ------------------------------------------------------------------

    def save_as_template(
        self,
        flow_dict: Dict,
        name: str,
        description: str,
        category: str = "Custom",
        tags: Optional[List[str]] = None,
        difficulty: str = "intermediate",
        required_services: Optional[List[str]] = None,
    ) -> str:
        """Sauvegarder un flux comme template.

        Returns the filepath of the saved template.
        """
        template_id = f"custom_{uuid.uuid4().hex[:8]}"

        template_data = {
            "id": template_id,
            "name": name,
            "version": "1.0.0",
            "description": description,
            "author": "OpenPaw",
            "category": category,
            "tags": tags or [],
            "difficulty": difficulty,
            "required_services": required_services or [],
            "parameters": {},
            "tasks": flow_dict.get("tasks", {}),
            "relations": flow_dict.get("relations", []),
            "entries": flow_dict.get("entries", []),
            "exits": flow_dict.get("exits", []),
            "variables": flow_dict.get("variables", {}),
            "groups": flow_dict.get("groups", {}),
        }

        filepath = Path(self.templates_dir) / f"{template_id}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(template_data, f, indent=2, ensure_ascii=False)

        return str(filepath)

    def export_template(self, template_id: str) -> str:
        """Export a template as a JSON string.

        Returns JSON string representation.
        Raises FileNotFoundError if template not found.
        """
        template = self.get_template(template_id)
        if template is None:
            raise FileNotFoundError(f"Template non trouve: {template_id}")
        return json.dumps(template, indent=2, ensure_ascii=False)

    def import_template_from_json(self, json_str: str) -> str:
        """Import a template from a JSON string.

        Returns the template ID.
        """
        data = json.loads(json_str)
        template_id = data.get("id", f"imported_{uuid.uuid4().hex[:8]}")

        # Ensure it has an id
        data["id"] = template_id

        filepath = Path(self.templates_dir) / f"{template_id}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return template_id

    def import_template_from_url(self, url: str) -> str:
        """Import a template from a URL (download JSON).

        Returns the template ID.
        """
        import urllib.request

        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read().decode("utf-8")
            return self.import_template_from_json(content)
        except Exception as e:
            raise RuntimeError(f"Failed to import template from URL: {e}")

    def delete_template(self, template_id: str) -> bool:
        """Delete a custom (file-based) template. Cannot delete builtins.

        Returns True if deleted, False if not found.
        """
        # Prevent deleting builtins
        for t in self.get_builtin_templates():
            if t["id"] == template_id:
                raise ValueError("Cannot delete builtin templates")

        filepath = Path(self.templates_dir) / f"{template_id}.json"
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _template_summary(self, template: Dict, builtin: bool = False) -> Dict:
        """Create a summary dict from a full template."""
        return {
            "id": template["id"],
            "name": template["name"],
            "description": template.get("description", ""),
            "category": template.get("category", "Custom"),
            "tags": template.get("tags", []),
            "difficulty": template.get("difficulty", "intermediate"),
            "required_services": template.get("required_services", []),
            "author": template.get("author", "OpenPaw"),
            "task_count": len(template.get("tasks", {})),
            "builtin": builtin,
        }

    # ------------------------------------------------------------------
    # Builtin templates
    # ------------------------------------------------------------------

    def get_builtin_templates(self) -> List[Dict]:
        """Templates integres."""
        return [
            self._simple_pipeline(),
            self._http_processor(),
            self._file_watcher(),
            self._json_splitter(),
            self._dedup_pipeline(),
            self._etl_database(),
            self._log_aggregator(),
            self._email_alert_pipeline(),
            self._kafka_stream_processor(),
            self._s3_backup_pipeline(),
            self._data_validation(),
            self._csv_to_json_converter(),
            self._ai_content_enrichment(),
            self._mqtt_iot_pipeline(),
            self._webhook_receiver(),
            self._agent_llm(),
        ]

    # --- Original 5 templates (with added metadata) ---

    def _simple_pipeline(self) -> Dict:
        return {
            "id": "builtin_simple_pipeline",
            "name": "Simple Pipeline",
            "version": "1.0.0",
            "description": "Pipeline simple: getFile -> transformJSON -> putFile",
            "author": "OpenPaw",
            "category": "Data Processing",
            "tags": ["file", "json", "transform", "beginner"],
            "difficulty": "beginner",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./input", "pattern": "*.json"},
                },
                "transformJSON_1": {
                    "type": "transformJSON",
                    "parameters": {"script": "def transform(data):\n    return data"},
                },
                "putFile_1": {
                    "type": "putFile",
                    "parameters": {"output_directory": "./output"},
                },
            },
            "relations": [
                {"from": "getFile_1", "to": "transformJSON_1", "type": "success"},
                {"from": "transformJSON_1", "to": "putFile_1", "type": "success"},
            ],
            "entries": ["getFile_1"],
            "exits": ["putFile_1"],
            "variables": {},
            "groups": {},
        }

    def _http_processor(self) -> Dict:
        return {
            "id": "builtin_http_processor",
            "name": "HTTP Processor",
            "version": "1.0.0",
            "description": "Traitement HTTP: fetchHTTP -> validateJSON -> routeOnAttribute -> logs",
            "author": "OpenPaw",
            "category": "Integration",
            "tags": ["http", "api", "validation", "routing"],
            "difficulty": "intermediate",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "fetchHTTP_1": {
                    "type": "fetchHTTP",
                    "parameters": {"url": "https://api.example.com/data", "method": "GET"},
                },
                "validateJSON_1": {
                    "type": "validateJSON",
                    "parameters": {},
                },
                "routeOnAttribute_1": {
                    "type": "routeOnAttribute",
                    "parameters": {"attribute": "validation.valid", "rules": []},
                },
                "log_success": {
                    "type": "log",
                    "parameters": {"message": "JSON valide", "level": "INFO"},
                },
                "log_failure": {
                    "type": "log",
                    "parameters": {"message": "JSON invalide", "level": "ERROR"},
                },
            },
            "relations": [
                {"from": "fetchHTTP_1", "to": "validateJSON_1", "type": "success"},
                {"from": "validateJSON_1", "to": "routeOnAttribute_1", "type": "success"},
                {"from": "routeOnAttribute_1", "to": "log_success", "type": "matched"},
                {"from": "routeOnAttribute_1", "to": "log_failure", "type": "unmatched"},
            ],
            "entries": ["fetchHTTP_1"],
            "exits": ["log_success", "log_failure"],
            "variables": {},
            "groups": {},
        }

    def _file_watcher(self) -> Dict:
        return {
            "id": "builtin_file_watcher",
            "name": "File Watcher",
            "version": "1.0.0",
            "description": "Surveillance de fichiers: listFiles -> getFile -> hashContent -> putFile",
            "author": "OpenPaw",
            "category": "Monitoring",
            "tags": ["file", "watch", "hash", "archive"],
            "difficulty": "beginner",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "listFiles_1": {
                    "type": "listFiles",
                    "parameters": {"path": "./watched", "pattern": "*"},
                },
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./watched"},
                },
                "hashContent_1": {
                    "type": "hashContent",
                    "parameters": {"algorithm": "sha256"},
                },
                "putFile_1": {
                    "type": "putFile",
                    "parameters": {"output_directory": "./archived"},
                },
            },
            "relations": [
                {"from": "listFiles_1", "to": "getFile_1", "type": "success"},
                {"from": "getFile_1", "to": "hashContent_1", "type": "success"},
                {"from": "hashContent_1", "to": "putFile_1", "type": "success"},
            ],
            "entries": ["listFiles_1"],
            "exits": ["putFile_1"],
            "variables": {},
            "groups": {},
        }

    def _json_splitter(self) -> Dict:
        return {
            "id": "builtin_json_splitter",
            "name": "JSON Array Splitter",
            "version": "1.0.0",
            "description": "Split JSON array -> transform each element -> merge results",
            "author": "OpenPaw",
            "category": "Data Processing",
            "tags": ["json", "split", "merge", "transform"],
            "difficulty": "intermediate",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./input", "pattern": "*.json"},
                },
                "splitJSON_1": {
                    "type": "splitJSON",
                    "parameters": {"json_path_expression": "$"},
                },
                "transformJSON_1": {
                    "type": "transformJSON",
                    "parameters": {"script": "def transform(data):\n    return data"},
                },
                "mergeContent_1": {
                    "type": "mergeContent",
                    "parameters": {"delimiter": "\n", "header": "[", "footer": "]"},
                },
                "putFile_1": {
                    "type": "putFile",
                    "parameters": {"output_directory": "./output"},
                },
            },
            "relations": [
                {"from": "getFile_1", "to": "splitJSON_1", "type": "success"},
                {"from": "splitJSON_1", "to": "transformJSON_1", "type": "success"},
                {"from": "transformJSON_1", "to": "mergeContent_1", "type": "success"},
                {"from": "mergeContent_1", "to": "putFile_1", "type": "success"},
            ],
            "entries": ["getFile_1"],
            "exits": ["putFile_1"],
            "variables": {},
            "groups": {},
        }

    def _dedup_pipeline(self) -> Dict:
        return {
            "id": "builtin_dedup_pipeline",
            "name": "Deduplication Pipeline",
            "version": "1.0.0",
            "description": "Detect and filter duplicate records using distributed cache",
            "author": "OpenPaw",
            "category": "Data Processing",
            "tags": ["dedup", "duplicate", "cache", "filter"],
            "difficulty": "intermediate",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./input", "pattern": "*.json"},
                },
                "splitJSON_1": {
                    "type": "splitJSON",
                    "parameters": {"json_path_expression": "$"},
                },
                "detectDuplicate_1": {
                    "type": "detectDuplicate",
                    "parameters": {},
                },
                "log_new": {
                    "type": "log",
                    "parameters": {"message": "New record processed", "level": "INFO"},
                },
                "log_dup": {
                    "type": "log",
                    "parameters": {"message": "Duplicate detected", "level": "WARNING"},
                },
                "putFile_1": {
                    "type": "putFile",
                    "parameters": {"output_directory": "./output"},
                },
            },
            "relations": [
                {"from": "getFile_1", "to": "splitJSON_1", "type": "success"},
                {"from": "splitJSON_1", "to": "detectDuplicate_1", "type": "success"},
                {"from": "detectDuplicate_1", "to": "log_new", "type": "success"},
                {"from": "detectDuplicate_1", "to": "log_dup", "type": "success"},
                {"from": "log_new", "to": "putFile_1", "type": "success"},
            ],
            "entries": ["getFile_1"],
            "exits": ["putFile_1", "log_dup"],
            "variables": {},
            "groups": {},
        }

    # --- New templates ---

    def _etl_database(self) -> Dict:
        return {
            "id": "builtin_etl_database",
            "name": "ETL Database",
            "version": "1.0.0",
            "description": "Extract data via HTTP, transform JSON, and load into database via SQL",
            "author": "OpenPaw",
            "category": "ETL",
            "tags": ["etl", "database", "sql", "http", "extract", "load"],
            "difficulty": "intermediate",
            "required_services": ["database"],
            "parameters": {},
            "tasks": {
                "fetchHTTP_1": {
                    "type": "fetchHTTP",
                    "parameters": {"url": "https://api.example.com/data", "method": "GET"},
                },
                "transformJSON_1": {
                    "type": "transformJSON",
                    "parameters": {
                        "script": "def transform(data):\n    # Map API fields to DB columns\n    return data"
                    },
                },
                "executeSQL_1": {
                    "type": "executeSQL",
                    "parameters": {
                        "sql": "INSERT INTO target_table (col1, col2) VALUES (:col1, :col2)",
                        "connection_string": "sqlite:///data.db",
                    },
                },
            },
            "relations": [
                {"from": "fetchHTTP_1", "to": "transformJSON_1", "type": "success"},
                {"from": "transformJSON_1", "to": "executeSQL_1", "type": "success"},
            ],
            "entries": ["fetchHTTP_1"],
            "exits": ["executeSQL_1"],
            "variables": {},
            "groups": {},
        }

    def _log_aggregator(self) -> Dict:
        return {
            "id": "builtin_log_aggregator",
            "name": "Log Aggregator",
            "version": "1.0.0",
            "description": "Aggregate log files: list, read, filter, merge, and write combined output",
            "author": "OpenPaw",
            "category": "Monitoring",
            "tags": ["logs", "aggregation", "filter", "merge", "monitoring"],
            "difficulty": "intermediate",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "listFiles_1": {
                    "type": "listFiles",
                    "parameters": {"path": "./logs", "pattern": "*.log"},
                },
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./logs"},
                },
                "filterContent_1": {
                    "type": "filterContent",
                    "parameters": {"pattern": "ERROR|WARN", "mode": "include"},
                },
                "mergeContent_1": {
                    "type": "mergeContent",
                    "parameters": {"delimiter": "\n", "header": "=== Aggregated Logs ===\n", "footer": "\n=== End ==="},
                },
                "putFile_1": {
                    "type": "putFile",
                    "parameters": {"output_directory": "./output/aggregated"},
                },
            },
            "relations": [
                {"from": "listFiles_1", "to": "getFile_1", "type": "success"},
                {"from": "getFile_1", "to": "filterContent_1", "type": "success"},
                {"from": "filterContent_1", "to": "mergeContent_1", "type": "success"},
                {"from": "mergeContent_1", "to": "putFile_1", "type": "success"},
            ],
            "entries": ["listFiles_1"],
            "exits": ["putFile_1"],
            "variables": {},
            "groups": {},
        }

    def _email_alert_pipeline(self) -> Dict:
        return {
            "id": "builtin_email_alert",
            "name": "Email Alert Pipeline",
            "version": "1.0.0",
            "description": "Monitor API endpoint, evaluate conditions, and send email alerts on threshold breach",
            "author": "OpenPaw",
            "category": "Communication",
            "tags": ["email", "alert", "monitoring", "notification", "http"],
            "difficulty": "intermediate",
            "required_services": ["smtp"],
            "parameters": {},
            "tasks": {
                "fetchHTTP_1": {
                    "type": "fetchHTTP",
                    "parameters": {"url": "https://api.example.com/metrics", "method": "GET"},
                },
                "evaluateJSONPath_1": {
                    "type": "evaluateJSONPath",
                    "parameters": {"expressions": {"alert_value": "$.metrics.error_rate"}},
                },
                "routeOnAttribute_1": {
                    "type": "routeOnAttribute",
                    "parameters": {
                        "attribute": "alert_value",
                        "rules": [{"value": "0.5", "comparison": "gt"}],
                    },
                },
                "sendEmail_1": {
                    "type": "sendEmail",
                    "parameters": {
                        "to": "ops-team@example.com",
                        "subject": "ALERT: Error rate threshold exceeded",
                        "body": "Error rate is above threshold. Check dashboard.",
                        "smtp_host": "smtp.example.com",
                        "smtp_port": 587,
                    },
                },
            },
            "relations": [
                {"from": "fetchHTTP_1", "to": "evaluateJSONPath_1", "type": "success"},
                {"from": "evaluateJSONPath_1", "to": "routeOnAttribute_1", "type": "success"},
                {"from": "routeOnAttribute_1", "to": "sendEmail_1", "type": "matched"},
            ],
            "entries": ["fetchHTTP_1"],
            "exits": ["sendEmail_1"],
            "variables": {},
            "groups": {},
        }

    def _kafka_stream_processor(self) -> Dict:
        return {
            "id": "builtin_kafka_stream",
            "name": "Kafka Stream Processor",
            "version": "1.0.0",
            "description": "Consume from Kafka, transform and route messages, publish to output topics",
            "author": "OpenPaw",
            "category": "Integration",
            "tags": ["kafka", "streaming", "transform", "routing", "messaging"],
            "difficulty": "advanced",
            "required_services": ["kafka"],
            "parameters": {},
            "tasks": {
                "consumeKafka_1": {
                    "type": "consumeKafka",
                    "parameters": {
                        "bootstrap_servers": "localhost:9092",
                        "topic": "input-events",
                        "group_id": "openpaw-processor",
                    },
                },
                "transformJSON_1": {
                    "type": "transformJSON",
                    "parameters": {
                        "script": "def transform(data):\n    data['processed'] = True\n    return data"
                    },
                },
                "routeOnAttribute_1": {
                    "type": "routeOnAttribute",
                    "parameters": {
                        "attribute": "event_type",
                        "rules": [{"value": "order", "comparison": "equals"}],
                    },
                },
                "publishKafka_1": {
                    "type": "publishKafka",
                    "parameters": {
                        "bootstrap_servers": "localhost:9092",
                        "topic": "processed-events",
                    },
                },
            },
            "relations": [
                {"from": "consumeKafka_1", "to": "transformJSON_1", "type": "success"},
                {"from": "transformJSON_1", "to": "routeOnAttribute_1", "type": "success"},
                {"from": "routeOnAttribute_1", "to": "publishKafka_1", "type": "matched"},
            ],
            "entries": ["consumeKafka_1"],
            "exits": ["publishKafka_1"],
            "variables": {},
            "groups": {},
        }

    def _s3_backup_pipeline(self) -> Dict:
        return {
            "id": "builtin_s3_backup",
            "name": "S3 Backup Pipeline",
            "version": "1.0.0",
            "description": "Scan local files, compress them, and upload to S3 for backup",
            "author": "OpenPaw",
            "category": "ETL",
            "tags": ["s3", "backup", "compress", "cloud", "archive"],
            "difficulty": "intermediate",
            "required_services": ["aws_s3"],
            "parameters": {},
            "tasks": {
                "listFiles_1": {
                    "type": "listFiles",
                    "parameters": {"path": "./data", "pattern": "*"},
                },
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./data"},
                },
                "compressContent_1": {
                    "type": "compressContent",
                    "parameters": {"format": "gzip"},
                },
                "putS3_1": {
                    "type": "putS3",
                    "parameters": {
                        "bucket": "my-backup-bucket",
                        "prefix": "backups/",
                        "region": "us-east-1",
                    },
                },
            },
            "relations": [
                {"from": "listFiles_1", "to": "getFile_1", "type": "success"},
                {"from": "getFile_1", "to": "compressContent_1", "type": "success"},
                {"from": "compressContent_1", "to": "putS3_1", "type": "success"},
            ],
            "entries": ["listFiles_1"],
            "exits": ["putS3_1"],
            "variables": {},
            "groups": {},
        }

    def _data_validation(self) -> Dict:
        return {
            "id": "builtin_data_validation",
            "name": "Data Validation",
            "version": "1.0.0",
            "description": "Read files, validate JSON structure, route valid records to output and invalid to error log",
            "author": "OpenPaw",
            "category": "Data Processing",
            "tags": ["validation", "json", "quality", "routing", "error-handling"],
            "difficulty": "beginner",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./input", "pattern": "*.json"},
                },
                "validateJSON_1": {
                    "type": "validateJSON",
                    "parameters": {},
                },
                "routeOnAttribute_1": {
                    "type": "routeOnAttribute",
                    "parameters": {
                        "attribute": "validation.valid",
                        "rules": [{"value": "true", "comparison": "equals"}],
                    },
                },
                "log_invalid": {
                    "type": "log",
                    "parameters": {"message": "Invalid JSON record detected", "level": "ERROR"},
                },
                "putFile_1": {
                    "type": "putFile",
                    "parameters": {"output_directory": "./output/valid"},
                },
            },
            "relations": [
                {"from": "getFile_1", "to": "validateJSON_1", "type": "success"},
                {"from": "validateJSON_1", "to": "routeOnAttribute_1", "type": "success"},
                {"from": "routeOnAttribute_1", "to": "putFile_1", "type": "matched"},
                {"from": "routeOnAttribute_1", "to": "log_invalid", "type": "unmatched"},
            ],
            "entries": ["getFile_1"],
            "exits": ["putFile_1", "log_invalid"],
            "variables": {},
            "groups": {},
        }

    def _csv_to_json_converter(self) -> Dict:
        return {
            "id": "builtin_csv_to_json",
            "name": "CSV to JSON Converter",
            "version": "1.0.0",
            "description": "Read CSV files, convert to JSON, transform structure, and write output",
            "author": "OpenPaw",
            "category": "Data Processing",
            "tags": ["csv", "json", "convert", "transform", "etl"],
            "difficulty": "beginner",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./input", "pattern": "*.csv"},
                },
                "convertCSVToJSON_1": {
                    "type": "convertCSVToJSON",
                    "parameters": {"delimiter": ",", "header": True},
                },
                "transformJSON_1": {
                    "type": "transformJSON",
                    "parameters": {"script": "def transform(data):\n    return data"},
                },
                "putFile_1": {
                    "type": "putFile",
                    "parameters": {"output_directory": "./output"},
                },
            },
            "relations": [
                {"from": "getFile_1", "to": "convertCSVToJSON_1", "type": "success"},
                {"from": "convertCSVToJSON_1", "to": "transformJSON_1", "type": "success"},
                {"from": "transformJSON_1", "to": "putFile_1", "type": "success"},
            ],
            "entries": ["getFile_1"],
            "exits": ["putFile_1"],
            "variables": {},
            "groups": {},
        }

    def _ai_content_enrichment(self) -> Dict:
        return {
            "id": "builtin_ai_enrichment",
            "name": "AI Content Enrichment",
            "version": "1.0.0",
            "description": "Read data files, enrich content using LLM inference, transform and save results",
            "author": "OpenPaw",
            "category": "Data Processing",
            "tags": ["ai", "llm", "enrichment", "inference", "nlp"],
            "difficulty": "advanced",
            "required_services": ["llm"],
            "parameters": {},
            "tasks": {
                "getFile_1": {
                    "type": "getFile",
                    "parameters": {"path": "./input", "pattern": "*.json"},
                },
                "inferLLM_1": {
                    "type": "inferLLM",
                    "parameters": {
                        "prompt_template": "Analyze the following data and extract key entities:\n\n${content}",
                        "model": "gpt-3.5-turbo",
                    },
                },
                "transformJSON_1": {
                    "type": "transformJSON",
                    "parameters": {
                        "script": "def transform(data):\n    # Merge AI output with original data\n    return data"
                    },
                },
                "putFile_1": {
                    "type": "putFile",
                    "parameters": {"output_directory": "./output/enriched"},
                },
            },
            "relations": [
                {"from": "getFile_1", "to": "inferLLM_1", "type": "success"},
                {"from": "inferLLM_1", "to": "transformJSON_1", "type": "success"},
                {"from": "transformJSON_1", "to": "putFile_1", "type": "success"},
            ],
            "entries": ["getFile_1"],
            "exits": ["putFile_1"],
            "variables": {},
            "groups": {},
        }

    def _mqtt_iot_pipeline(self) -> Dict:
        return {
            "id": "builtin_mqtt_iot",
            "name": "MQTT IoT Pipeline",
            "version": "1.0.0",
            "description": "Consume IoT sensor data via MQTT, transform, extract metrics, and store in S3",
            "author": "OpenPaw",
            "category": "Integration",
            "tags": ["mqtt", "iot", "sensors", "s3", "telemetry"],
            "difficulty": "advanced",
            "required_services": ["mqtt", "aws_s3"],
            "parameters": {},
            "tasks": {
                "consumeMQTT_1": {
                    "type": "consumeMQTT",
                    "parameters": {
                        "broker": "mqtt.example.com",
                        "port": 1883,
                        "topic": "sensors/#",
                    },
                },
                "transformJSON_1": {
                    "type": "transformJSON",
                    "parameters": {
                        "script": "def transform(data):\n    data['ingested_at'] = '${now()}'\n    return data"
                    },
                },
                "evaluateJSONPath_1": {
                    "type": "evaluateJSONPath",
                    "parameters": {
                        "expressions": {
                            "sensor_id": "$.sensor_id",
                            "temperature": "$.readings.temperature",
                        }
                    },
                },
                "putS3_1": {
                    "type": "putS3",
                    "parameters": {
                        "bucket": "iot-data-lake",
                        "prefix": "raw/sensors/",
                        "region": "us-east-1",
                    },
                },
            },
            "relations": [
                {"from": "consumeMQTT_1", "to": "transformJSON_1", "type": "success"},
                {"from": "transformJSON_1", "to": "evaluateJSONPath_1", "type": "success"},
                {"from": "evaluateJSONPath_1", "to": "putS3_1", "type": "success"},
            ],
            "entries": ["consumeMQTT_1"],
            "exits": ["putS3_1"],
            "variables": {},
            "groups": {},
        }

    def _agent_llm(self) -> Dict:
        return {
            "id": "builtin_agent_llm",
            "name": "Agent LLM",
            "version": "1.0.0",
            "description": "HTTP-exposed LLM agent with tool-use loop: receive request, run agent, return response",
            "author": "OpenPaw",
            "category": "Integration",
            "tags": ["agent", "llm", "ai", "tool-use", "http", "chatbot"],
            "difficulty": "advanced",
            "required_services": ["llm", "httpListener"],
            "parameters": {
                "api_key": "",
                "model": "",
                "provider": "openai",
            },
            "tasks": {
                "httpReceiver_1": {
                    "type": "httpReceiver",
                    "parameters": {
                        "port": 8888,
                        "method": "POST",
                        "path": "/api/agent",
                        "response_timeout": 120,
                    },
                },
                "agentLoop_1": {
                    "type": "agentLoop",
                    "parameters": {
                        "provider": "${flow.parameters.provider}",
                        "api_key": "${flow.parameters.api_key}",
                        "model": "${flow.parameters.model}",
                        "system_prompt": "You are a helpful assistant with access to tools.",
                        "max_iterations": 10,
                        "max_tokens": 4096,
                    },
                },
                "handleHTTPResponse_1": {
                    "type": "handleHTTPResponse",
                    "parameters": {
                        "status_code": 200,
                        "content_type": "application/json",
                    },
                },
            },
            "relations": [
                {"from": "httpReceiver_1", "to": "agentLoop_1", "type": "success"},
                {"from": "agentLoop_1", "to": "handleHTTPResponse_1", "type": "success"},
            ],
            "entries": ["httpReceiver_1"],
            "exits": ["handleHTTPResponse_1"],
            "variables": {},
            "groups": {},
        }

    def _webhook_receiver(self) -> Dict:
        return {
            "id": "builtin_webhook_receiver",
            "name": "Webhook Receiver",
            "version": "1.0.0",
            "description": "Listen for incoming webhooks, validate payload, route by type, and log events",
            "author": "OpenPaw",
            "category": "Integration",
            "tags": ["webhook", "http", "listener", "validation", "routing"],
            "difficulty": "intermediate",
            "required_services": [],
            "parameters": {},
            "tasks": {
                "listenHTTP_1": {
                    "type": "listenHTTP",
                    "parameters": {
                        "port": 8080,
                        "path": "/webhook",
                        "method": "POST",
                    },
                },
                "validateJSON_1": {
                    "type": "validateJSON",
                    "parameters": {},
                },
                "routeOnAttribute_1": {
                    "type": "routeOnAttribute",
                    "parameters": {
                        "attribute": "event_type",
                        "rules": [{"value": "push", "comparison": "equals"}],
                    },
                },
                "log_event": {
                    "type": "log",
                    "parameters": {"message": "Webhook event received and processed", "level": "INFO"},
                },
            },
            "relations": [
                {"from": "listenHTTP_1", "to": "validateJSON_1", "type": "success"},
                {"from": "validateJSON_1", "to": "routeOnAttribute_1", "type": "success"},
                {"from": "routeOnAttribute_1", "to": "log_event", "type": "matched"},
            ],
            "entries": ["listenHTTP_1"],
            "exits": ["log_event"],
            "variables": {},
            "groups": {},
        }
