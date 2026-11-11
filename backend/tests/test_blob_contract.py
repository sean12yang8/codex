import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from backend.blob_source import read_export
from backend.operations import AzureFailure


def test_manifest_requires_orderable_execution_timestamp():
    tenant = SimpleNamespace(id="10000000-0000-4000-8000-000000000001")
    sub = SimpleNamespace(id="20000000-0000-4000-8000-000000000001")
    listing = b"<EnumerationResults><Blobs><Blob><Name>export/manifest.json</Name></Blob></Blobs></EnumerationResults>"
    manifest = {
        "runInfo": {
            "startDate": "2026-11-01",
            "endDate": "2026-11-01",
            "runId": "unordered-guid",
        },
        "blobs": [{"blobName": "export/cost.csv", "byteCount": 1}],
    }
    responses = [
        SimpleNamespace(ok=True, status_code=200, content=listing),
        SimpleNamespace(
            ok=True, status_code=200, content=json.dumps(manifest).encode()
        ),
    ]
    for response in responses:
        response.iter_content = lambda chunk_size, content=response.content: [content]
        response.close = lambda: None
    with (
        patch("backend.blob_source.tenant_token", return_value="fixture-token"),
        patch("backend.blob_source.requests.get", side_effect=responses),
    ):
        with pytest.raises(AzureFailure, match="UNSUPPORTED_EXPORT_MANIFEST"):
            read_export(
                tenant,
                sub,
                {
                    "account": "fixturestorage",
                    "container": "exports",
                    "prefix": "export/",
                },
                "2026-11-01",
                "2026-11-01",
            )


def test_manifest_checks_shards_and_does_not_invent_zero_days():
    tenant = SimpleNamespace(id="10000000-0000-4000-8000-000000000001")
    sub = SimpleNamespace(id="20000000-0000-4000-8000-000000000001")
    listing = b"<EnumerationResults><Blobs><Blob><Name>export/manifest.json</Name></Blob></Blobs></EnumerationResults>"
    csv = b"subscription_id,usage_date,service,currency,amount\n20000000-0000-4000-8000-000000000001,2026-11-01,Compute,USD,100\n"
    manifest = {
        "runInfo": {
            "startDate": "2026-11-01",
            "endDate": "2026-11-02",
            "executionTime": "2026-11-03T00:00:00Z",
        },
        "blobs": [{"blobName": "export/cost.csv", "byteCount": len(csv)}],
    }
    responses = [
        SimpleNamespace(ok=True, status_code=200, content=x)
        for x in [listing, json.dumps(manifest).encode(), csv]
    ]
    for response in responses:
        response.iter_content = lambda chunk_size, content=response.content: [content]
        response.close = lambda: None
    with (
        patch("backend.blob_source.tenant_token", return_value="fixture-token"),
        patch("backend.blob_source.requests.get", side_effect=responses),
    ):
        rows, complete, evidence = read_export(
            tenant,
            sub,
            {"account": "fixturestorage", "container": "exports", "prefix": "export/"},
            "2026-11-01",
            "2026-11-02",
        )
        assert len(rows) == 1 and complete is False and "Manifest" in evidence
