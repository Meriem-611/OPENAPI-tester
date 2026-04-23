"""Unit tests for spec parsing helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from spec_parser import OpenAPISpecParser


class SpecParserTests(unittest.TestCase):
    def test_extract_resource_group_simple(self) -> None:
        self.assertEqual(OpenAPISpecParser.extract_resource_group("/users/{id}"), "users")

    def test_extract_resource_group_nested(self) -> None:
        self.assertEqual(OpenAPISpecParser.extract_resource_group("/orders/{id}/items"), "orders")

    def test_extract_resource_group_root(self) -> None:
        self.assertEqual(OpenAPISpecParser.extract_resource_group("/{id}"), "root")

    def test_parses_path_level_servers_into_metadata(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "x", "version": "1"},
            "paths": {
                "/users": {
                    "servers": [{"url": "https://example.org"}],
                    "get": {"responses": {"200": {"description": "ok"}}},
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            parser = OpenAPISpecParser(spec_path)
            _, metadata = parser.parse()
            self.assertEqual(metadata["discovered_servers"][0]["url"], "https://example.org")

    def test_cyclic_ref_does_not_crash(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "x", "version": "1"},
            "paths": {
                "/node": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Node"}
                                    }
                                },
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {"child": {"$ref": "#/components/schemas/Node"}},
                    }
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            parser = OpenAPISpecParser(spec_path)
            operations, _ = parser.parse()
            self.assertEqual(len(operations), 1)


if __name__ == "__main__":
    unittest.main()
