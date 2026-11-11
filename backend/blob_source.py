from datetime import datetime, timezone

"""Read-only Azure Export REST adapter. Requires an explicit complete manifest contract."""

import csv
import gzip
import io
import json
from urllib.parse import quote
from xml.etree import ElementTree
import requests
from backend.operations import AzureFailure, tenant_token, normalize_csv


def read_export(tenant, sub, config, start, end):
    account = config["account"]
    container = config["container"]
    prefix = config.get("prefix", "")
    origin = f"https://{account}.blob.core.windows.net"
    token = tenant_token(tenant, "https://storage.azure.com/.default")
    headers = {"Authorization": "Bearer " + token, "x-ms-version": "2023-11-03"}

    def get(path, params=None):
        try:
            r = requests.get(
                origin + "/" + quote(container) + "/" + quote(path, safe="/"),
                headers=headers,
                params=params,
                timeout=(10, 60),
                allow_redirects=False,
                stream=True,
            )
            try:
                if r.status_code in [401, 403]:
                    raise AzureFailure("BLOB_ACCESS_DENIED")
                if not r.ok:
                    raise AzureFailure("BLOB_HTTP_" + str(r.status_code))
                parts = []
                total = 0
                for chunk in r.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise AzureFailure("BLOB_FILE_TOO_LARGE")
                    parts.append(chunk)
                return b"".join(parts)
            finally:
                r.close()
        except requests.RequestException:
            raise AzureFailure("BLOB_NETWORK_ERROR") from None

    marker = ""
    manifests = []
    for _ in range(100):
        content = get(
            "",
            {
                "restype": "container",
                "comp": "list",
                "prefix": prefix,
                "marker": marker,
                "maxresults": "5000",
            },
        )
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            raise AzureFailure("INVALID_BLOB_LIST") from None
        for node in root.findall("./Blobs/Blob"):
            name = node.findtext("Name")
            if name and name.lower().endswith("manifest.json"):
                manifests.append(name)
        marker = root.findtext("NextMarker") or ""
        if not marker:
            break
    else:
        raise AzureFailure("BLOB_LIST_LIMIT")
    candidates = []
    for name in manifests:
        try:
            doc = json.loads(get(name))
            run = doc["runInfo"]
            a = run["startDate"][:10]
            b = run["endDate"][:10]
            version = run.get("executionTime")
            if not isinstance(version, str):
                raise ValueError()
            timestamp = datetime.fromisoformat(version.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError()
            version = timestamp.astimezone(timezone.utc).isoformat()
            if not isinstance(version, str) or not doc.get("blobs"):
                raise KeyError()
            if a <= start and b >= end:
                candidates.append((version, name, doc))
        except (ValueError, KeyError, TypeError):
            raise AzureFailure("UNSUPPORTED_EXPORT_MANIFEST") from None
    if not candidates:
        return None, False, ""  # API fills ranges with no covering export
    version, name, doc = max(candidates, key=lambda x: x[0])
    rows = []
    for blob in doc["blobs"]:
        path = blob.get("blobName")
        size = blob.get("byteCount")
        if (
            not isinstance(path, str)
            or not path.startswith(prefix)
            or not isinstance(size, int)
        ):
            raise AzureFailure("MANIFEST_INCOMPLETE")
        if size > 100 * 1024 * 1024:
            raise AzureFailure("BLOB_FILE_TOO_LARGE")
        content = get(path)
        if len(content) != size:
            raise AzureFailure("MANIFEST_SIZE_MISMATCH")
        if path.endswith(".gz"):
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as f:
                content = f.read(100 * 1024 * 1024 + 1)
            if len(content) > 100 * 1024 * 1024:
                raise AzureFailure("BLOB_FILE_TOO_LARGE")
        standard = normalize_csv(content)
        for row in csv.DictReader(io.StringIO(standard.decode())):
            if row["subscription_id"] == sub.id and start <= row["usage_date"] <= end:
                rows.append(row)
    # A full upstream manifest is necessary but may contain mixed scopes. Require
    # explicit rows for each day; no fabricated zero amounts from absence.
    present = {r["usage_date"] for r in rows}
    from backend.app import days

    complete = present == set(days(start, end))
    return (
        rows,
        complete,
        "Manifest " + name + "; run " + version + "; all declared shard sizes verified",
    )
