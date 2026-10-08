"""Parity with barryw/PaperlessMCP v0.6.0: same 44 tool names, same parameter
names per tool — ported from PaperlessMCP.Tests/Tools/ToolNamingTests.cs plus
a parameter-name snapshot taken directly from each *Tools.cs signature.
"""

import pytest

from paperless_mcp_oidc.server import mcp

EXPECTED_TOOL_NAMES = {
    "paperless_capabilities",
    "paperless_ping",
    "paperless_documents_search",
    "paperless_documents_get",
    "paperless_documents_download",
    "paperless_documents_preview",
    "paperless_documents_thumbnail",
    "paperless_documents_thumbnail_image",
    "paperless_documents_upload",
    "paperless_documents_upload_from_path",
    "paperless_documents_update",
    "paperless_documents_delete",
    "paperless_documents_bulk_update",
    "paperless_documents_reprocess",
    "paperless_documents_export_to_outbox",
    "paperless_tags_list",
    "paperless_tags_get",
    "paperless_tags_create",
    "paperless_tags_update",
    "paperless_tags_delete",
    "paperless_tags_bulk_delete",
    "paperless_correspondents_list",
    "paperless_correspondents_get",
    "paperless_correspondents_create",
    "paperless_correspondents_update",
    "paperless_correspondents_delete",
    "paperless_correspondents_bulk_delete",
    "paperless_document_types_list",
    "paperless_document_types_get",
    "paperless_document_types_create",
    "paperless_document_types_update",
    "paperless_document_types_delete",
    "paperless_document_types_bulk_delete",
    "paperless_storage_paths_list",
    "paperless_storage_paths_get",
    "paperless_storage_paths_create",
    "paperless_storage_paths_update",
    "paperless_storage_paths_delete",
    "paperless_storage_paths_bulk_delete",
    "paperless_custom_fields_list",
    "paperless_custom_fields_get",
    "paperless_custom_fields_create",
    "paperless_custom_fields_update",
    "paperless_custom_fields_delete",
    "paperless_custom_fields_assign",
}

# Parameter-name snapshot taken from each *Tools.cs method signature (the
# `PaperlessClient client` DI parameter is excluded there too — the C# SDK
# never puts it in the tool schema, matching get_client() being called
# internally here).
EXPECTED_PARAMS: dict[str, set[str]] = {
    "paperless_capabilities": set(),
    "paperless_ping": set(),
    "paperless_documents_search": {
        "query", "tags", "tagsExclude", "correspondent", "documentType", "storagePath",
        "createdAfter", "createdBefore", "addedAfter", "addedBefore", "archiveSerialNumber",
        "page", "pageSize", "ordering", "customFieldQuery", "includeContent", "contentMaxLength",
    },
    "paperless_documents_get": {"id"},
    "paperless_documents_download": {"id", "returnBase64"},
    "paperless_documents_preview": {"id"},
    "paperless_documents_thumbnail": {"id"},
    "paperless_documents_thumbnail_image": {"id"},
    "paperless_documents_upload": {
        "fileContent", "fileName", "title", "correspondent", "documentType", "storagePath",
        "tags", "archiveSerialNumber", "created",
    },
    "paperless_documents_upload_from_path": {
        "filePath", "title", "correspondent", "documentType", "storagePath",
        "tags", "archiveSerialNumber", "created",
    },
    "paperless_documents_update": {
        "id", "title", "correspondent", "documentType", "storagePath", "tags",
        "archiveSerialNumber", "created", "includeContent",
    },
    "paperless_documents_delete": {"id", "confirm"},
    "paperless_documents_bulk_update": {"documentIds", "operation", "value", "dryRun", "confirm"},
    "paperless_documents_reprocess": {"id", "confirm"},
    "paperless_documents_export_to_outbox": {"id", "filename", "original"},
    "paperless_tags_list": {"page", "pageSize", "ordering"},
    "paperless_tags_get": {"id"},
    "paperless_tags_create": {"name", "color", "match", "matchingAlgorithm", "isInboxTag", "parent"},
    "paperless_tags_update": {
        "id", "name", "color", "match", "matchingAlgorithm", "isInboxTag", "parent", "clearParent",
    },
    "paperless_tags_delete": {"id", "confirm"},
    "paperless_tags_bulk_delete": {"tagIds", "dryRun", "confirm"},
    "paperless_correspondents_list": {"page", "pageSize", "ordering"},
    "paperless_correspondents_get": {"id"},
    "paperless_correspondents_create": {"name", "match", "matchingAlgorithm"},
    "paperless_correspondents_update": {"id", "name", "match", "matchingAlgorithm"},
    "paperless_correspondents_delete": {"id", "confirm"},
    "paperless_correspondents_bulk_delete": {"correspondentIds", "dryRun", "confirm"},
    "paperless_document_types_list": {"page", "pageSize", "ordering"},
    "paperless_document_types_get": {"id"},
    "paperless_document_types_create": {"name", "match", "matchingAlgorithm"},
    "paperless_document_types_update": {"id", "name", "match", "matchingAlgorithm"},
    "paperless_document_types_delete": {"id", "confirm"},
    "paperless_document_types_bulk_delete": {"documentTypeIds", "dryRun", "confirm"},
    "paperless_storage_paths_list": {"page", "pageSize", "ordering"},
    "paperless_storage_paths_get": {"id"},
    "paperless_storage_paths_create": {"name", "path", "match", "matchingAlgorithm"},
    "paperless_storage_paths_update": {"id", "name", "path", "match", "matchingAlgorithm"},
    "paperless_storage_paths_delete": {"id", "confirm"},
    "paperless_storage_paths_bulk_delete": {"storagePathIds", "dryRun", "confirm"},
    "paperless_custom_fields_list": {"page", "pageSize"},
    "paperless_custom_fields_get": {"id"},
    "paperless_custom_fields_create": {"name", "dataType", "selectOptions", "defaultCurrency"},
    "paperless_custom_fields_update": {"id", "name", "selectOptions", "defaultCurrency"},
    "paperless_custom_fields_delete": {"id", "confirm"},
    "paperless_custom_fields_assign": {"documentId", "fieldId", "value"},
}


@pytest.fixture(scope="module")
def registered_tools():
    import asyncio

    return asyncio.run(mcp.list_tools())


def test_tool_count_is_45(registered_tools):
    assert len(registered_tools) == 45


def test_tool_names_match_barryw_paperlessmcp_v0_6_0(registered_tools):
    names = {t.name for t in registered_tools}
    assert names == EXPECTED_TOOL_NAMES


def test_all_tool_names_match_anthropic_naming_rules(registered_tools):
    import re

    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
    violations = [t.name for t in registered_tools if not pattern.match(t.name)]
    assert violations == []


def test_no_tool_names_contain_dots(registered_tools):
    assert [t.name for t in registered_tools if "." in t.name] == []


def test_parameter_names_match_csharp_signatures(registered_tools):
    by_name = {t.name: t for t in registered_tools}
    mismatches = {}
    for name, expected in EXPECTED_PARAMS.items():
        actual = set(by_name[name].parameters.get("properties", {}).keys())
        if actual != expected:
            mismatches[name] = {"expected": expected, "actual": actual}
    assert mismatches == {}
