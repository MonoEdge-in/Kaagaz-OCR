"""Customer B can never reach customer A's files through S3Storage.

The storage client here is a small in-memory S3 (a dict of key to bytes), so
the test sees the exact keys S3Storage builds and what each customer's call
can touch. Every S3Storage call is scoped by the customer's own key prefix;
there is no call that takes a key from outside.
"""

import io
import unittest
from typing import Any

from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from kaagaz.adapters.storage import S3Storage, original_key, page_image_key, quarantine_key
from kaagaz.ingestion.ports import BlobNotFound
from tests.settings_factory import make_settings

A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
DOC = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"  # the same document id for both


def _missing(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, operation)


class MemoryS3:
    """The few S3 calls S3Storage makes, over a dict. Records every key used."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.keys_used: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: Any, **_: Any) -> dict[str, Any]:
        self.keys_used.append(Key)
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.read()
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.keys_used.append(Key)
        if Key not in self.objects:
            raise _missing("GetObject")
        data = self.objects[Key]
        return {"Body": StreamingBody(io.BytesIO(data), len(data))}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, **_: Any) -> dict[str, Any]:
        self.keys_used.append(Prefix)
        found = [{"Key": key} for key in sorted(self.objects) if key.startswith(Prefix)]
        return {"Contents": found, "IsTruncated": False}

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        for item in Delete["Objects"]:
            self.keys_used.append(item["Key"])
            self.objects.pop(item["Key"], None)
        return {}

    def copy_object(self, *, Bucket: str, Key: str, CopySource: dict[str, str]) -> dict[str, Any]:
        self.keys_used.append(CopySource["Key"])
        if CopySource["Key"] not in self.objects:
            raise _missing("CopyObject")
        self.objects[Key] = self.objects[CopySource["Key"]]
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.keys_used.append(Key)
        self.objects.pop(Key, None)
        return {}


class StorageIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.s3 = MemoryS3()
        self.storage = S3Storage(make_settings(), client=self.s3)
        self.storage.put(A, DOC, io.BytesIO(b"A's invoice"))
        self.storage.put_page_image(A, DOC, 1, b"A's page")
        self.a_objects = dict(self.s3.objects)
        self.s3.keys_used.clear()

    def _b_keys_used(self) -> list[str]:
        return list(self.s3.keys_used)

    def test_keys_for_two_customers_never_overlap(self) -> None:
        def keys(customer: str) -> set[str]:
            return {
                original_key(customer, DOC),
                page_image_key(customer, DOC, 1),
                quarantine_key(customer, DOC),
            }

        a_keys, b_keys = keys(A), keys(B)
        self.assertFalse(a_keys & b_keys)
        for key in b_keys:
            self.assertNotIn(A, key)
        for key in a_keys:
            self.assertNotIn(B, key)

    def test_b_cannot_get_a_file_with_a_document_id(self) -> None:
        with self.assertRaises(BlobNotFound):
            self.storage.get(B, DOC)
        with self.assertRaises(BlobNotFound):
            self.storage.get_page_image(B, DOC, 1)
        # B's calls only ever named keys under B's own prefix.
        self.assertTrue(self._b_keys_used())
        for key in self._b_keys_used():
            self.assertTrue(key.startswith(f"customers/{B}/"), key)
            self.assertNotIn(key, self.a_objects)

    def test_b_delete_leaves_a_files(self) -> None:
        self.storage.delete(B, DOC)
        self.assertEqual(self.s3.objects, self.a_objects)
        for key in self._b_keys_used():
            self.assertIn(B, key)
            self.assertNotIn(A, key)

    def test_b_quarantine_does_not_move_a_file(self) -> None:
        self.storage.quarantine(B, DOC)
        self.assertEqual(self.s3.objects, self.a_objects)
        self.assertEqual(self.storage.get(A, DOC), b"A's invoice")

    def test_a_crafted_id_cannot_climb_into_a_prefix(self) -> None:
        for crafted in (f"../../{A}/documents/{DOC}", f"{DOC}/../../../customers/{A}", A + "/x"):
            with self.subTest(crafted=crafted), self.assertRaises(ValueError):
                self.storage.get(B, crafted)
        self.assertEqual(self._b_keys_used(), [])

    def test_quarantined_file_is_out_of_reach_of_get(self) -> None:
        self.storage.quarantine(A, DOC)
        with self.assertRaises(BlobNotFound):
            self.storage.get(A, DOC)
        self.assertIn(quarantine_key(A, DOC), self.s3.objects)


if __name__ == "__main__":
    unittest.main()
