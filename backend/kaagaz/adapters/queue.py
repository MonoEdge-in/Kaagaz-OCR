"""The job queue over the SQS API: ElasticMQ on the laptop, Amazon SQS in AWS.

It implements ``JobQueue`` (upload puts a job in) and ``WorkQueue`` (the
worker takes it out), with the lease model described in kaagaz/jobs/ports.py:

* The message body is ``{"customer_id": ..., "document_id": ...}``: ids only,
  never a file or its name.
* ``receive`` long-polls for up to ``queue_wait_seconds`` and hides the job
  for ``queue_lease_seconds``. The attempt number is SQS's own receive count,
  so it counts crashed deliveries too.
* ``ack`` deletes the message; ``extend`` resets its lease.
* ``bury`` puts the job in the failure bin (``queue_url + "-failed"``) with its
  code, then deletes it. It first renews the lease, so a worker whose lease
  already ran out cannot put a job in the failure bin that another worker is
  still running.
* After the maximum number of receives, the queue itself moves a job to the
  failure bin: that is the redrive policy in infra/elasticmq/elasticmq.conf
  (and in the AWS queue's settings), not code here. ``max_receives`` reads
  that maximum from the queue, so the worker knows a job's last delivery.
* A stale receipt raises ``LeaseLost``. Any other queue failure is
  ``BackendUnavailable``.

A message that is not a job this code wrote (bad JSON, missing ids) is buried
at once with the code ``bad_message``, so it cannot block the queue.

PROVISIONAL: the lease length, the long-poll wait and the maximum receives
are Vrushit's queue settings (PRD section 6).
"""

import contextlib
import json
import logging
from collections import OrderedDict
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ports import Delivery, LeaseLost, QueueSetupError
from kaagaz.settings import Settings

logger = logging.getLogger(__name__)

_UNREACHABLE = (BotoConnectionError, HTTPClientError)
_ANY_ERROR = (ClientError, *_UNREACHABLE)

# What SQS and ElasticMQ answer for a receipt that is no longer current. SQS's
# JSON protocol can give either the short or the older "AWS.SimpleQueueService."
# form. InvalidParameterValue is what SQS gives for a visibility change on a
# message that is no longer leased; every other parameter here is fixed.
_STALE_RECEIPT_CODES = frozenset(
    {
        "ReceiptHandleIsInvalid",
        "AWS.SimpleQueueService.ReceiptHandleIsInvalid",
        "MessageNotInflight",
        "AWS.SimpleQueueService.MessageNotInflight",
        "InvalidParameterValue",
    }
)

# SQS limits (a larger value is refused by the queue on every call).
_MAX_WAIT_SECONDS = 20
_MAX_LEASE_SECONDS = 12 * 60 * 60

# bury needs the message body to put it in the failure bin, and only has the
# receipt, so the bodies of recent deliveries are kept here. The worker runs
# one job at a time, so only the latest is ever needed; a few more cover jobs
# left to time out (a retryable failure never calls ack or bury).
_REMEMBERED_DELIVERIES = 16


# AR15b: a queue that does not answer fails fast: 5 s to connect, and at most two
# retries (botocore's max_attempts counts retries: three tries in all), so a "read
# again" or an upload never hangs on the queue. Two clients:
# * sends (an upload, "read again", the failure bin) are one short request, so
#   their client reads for 5 s: about 30 s per send at worst, not 105 s
#   (at QUEUE_WAIT_SECONDS=20);
# * the other calls share a client whose read timeout leaves room for the long
#   poll (QUEUE_WAIT_SECONDS plus a margin), or every idle receive would time out.
_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_MARGIN_SECONDS = 10
_SEND_READ_TIMEOUT_SECONDS = 5
_MAX_ATTEMPTS = 2


def _make_client(settings: Settings, read_timeout_seconds: int) -> Any:
    # Credentials come from boto3's usual places: the task role in AWS, and the
    # laptop-only AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY for ElasticMQ,
    # which accepts any values (.env.example).
    return boto3.client(
        "sqs",
        endpoint_url=settings.queue_endpoint_url,
        region_name=settings.queue_region,
        config=Config(
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            read_timeout=read_timeout_seconds,
            retries={"max_attempts": _MAX_ATTEMPTS},
        ),
    )


def _job_from_body(body: str) -> Job | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    customer_id, document_id = data.get("customer_id"), data.get("document_id")
    if not isinstance(customer_id, str) or not isinstance(document_id, str):
        return None
    if not customer_id or not document_id:
        return None
    return Job(customer_id=customer_id, document_id=document_id)


def _max_receive_count(policy: object) -> int | None:
    """``maxReceiveCount`` from a RedrivePolicy's JSON text, as a whole number of
    at least 1, or None when there is no such count."""
    if not isinstance(policy, str):
        return None
    try:
        data = json.loads(policy)
    except ValueError:
        return None
    count = data.get("maxReceiveCount") if isinstance(data, dict) else None
    if isinstance(count, str) and count.isascii() and count.isdigit():
        count = int(count)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return None
    return count


class SqsQueue:
    def __init__(self, settings: Settings, client: Any = None) -> None:
        if not 1 <= settings.queue_wait_seconds <= _MAX_WAIT_SECONDS:
            # 0 would make an idle worker ask the queue in a tight loop.
            raise ValueError(f"QUEUE_WAIT_SECONDS must be 1 to {_MAX_WAIT_SECONDS}")
        if not 1 <= settings.queue_lease_seconds <= _MAX_LEASE_SECONDS:
            raise ValueError(f"QUEUE_LEASE_SECONDS must be 1 to {_MAX_LEASE_SECONDS}")
        # boto3 ships no type information, so the clients are Any. Tests pass one
        # client wrapped in botocore.stub.Stubber, which answers every call.
        if client is None:
            self._client = _make_client(settings, settings.queue_wait_seconds + _READ_TIMEOUT_MARGIN_SECONDS)
            self._send_client = _make_client(settings, _SEND_READ_TIMEOUT_SECONDS)
        else:
            self._client = self._send_client = client
        self._url = settings.queue_url
        self._failed_url = settings.queue_url + "-failed"
        self._wait = settings.queue_wait_seconds
        self._lease = settings.queue_lease_seconds
        self._bodies: OrderedDict[str, str] = OrderedDict()

    # --- JobQueue ----------------------------------------------------------

    def enqueue(self, job: Job) -> None:
        body = json.dumps({"customer_id": job.customer_id, "document_id": job.document_id})
        try:
            self._send_client.send_message(QueueUrl=self._url, MessageBody=body)
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error

    # --- WorkQueue ---------------------------------------------------------

    def receive(self) -> Delivery | None:
        """One job, leased to this worker, or None if none came within the wait."""
        try:
            response = self._client.receive_message(
                QueueUrl=self._url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=self._wait,
                VisibilityTimeout=self._lease,
                # Both names ask for the receive count: the new one for SQS,
                # the older one for queues that only know it (ElasticMQ).
                MessageSystemAttributeNames=["ApproximateReceiveCount"],
                AttributeNames=["ApproximateReceiveCount"],
            )
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error
        messages: list[dict[str, Any]] = response.get("Messages", [])
        if not messages:
            return None
        message = messages[0]
        receipt = str(message["ReceiptHandle"])
        body = str(message.get("Body", ""))
        self._remember(receipt, body)
        job = _job_from_body(body)
        if job is None:
            logger.warning("queue message is not a job", extra={"code": "bad_message"})
            # LeaseLost: another delivery of the same message is burying it.
            with contextlib.suppress(LeaseLost):
                self.bury(receipt, "bad_message")
            return None
        # Only the queue's redrive policy acts on the count, so a queue that
        # leaves it out cannot change what happens to the job.
        attempt = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        return Delivery(job=job, receipt=receipt, attempt=attempt)

    def ack(self, receipt: str) -> None:
        self._delete(receipt)
        self._bodies.pop(receipt, None)

    def extend(self, receipt: str) -> None:
        try:
            self._client.change_message_visibility(
                QueueUrl=self._url, ReceiptHandle=receipt, VisibilityTimeout=self._lease
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _STALE_RECEIPT_CODES:
                raise LeaseLost from None
            raise BackendUnavailable("queue") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("queue") from error

    def bury(self, receipt: str, code: str) -> None:
        body = self._bodies.get(receipt)
        if body is None:
            # Not a receipt from this queue object: a bug, never a normal case.
            raise ValueError("unknown receipt")
        self.extend(receipt)  # LeaseLost here: another delivery owns the job
        try:
            self._send_client.send_message(
                QueueUrl=self._failed_url,
                MessageBody=body,
                MessageAttributes={"failure_code": {"DataType": "String", "StringValue": code}},
            )
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error
        self._delete(receipt)
        self._bodies.pop(receipt, None)

    # --- Health ------------------------------------------------------------

    def ping(self) -> None:
        """Raise BackendUnavailable unless the queue answers."""
        try:
            self._client.get_queue_attributes(QueueUrl=self._url, AttributeNames=["QueueArn"])
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error

    # --- Settings ----------------------------------------------------------

    def max_receives(self) -> int:
        """The queue's maximum receives: ``maxReceiveCount`` in its redrive policy.

        The worker reads it once at start-up, to know which delivery of a job is
        the last (kaagaz/jobs/worker.py). SQS gives the count as a number or as a
        string, so both are read. A queue without a redrive policy is refused
        with QueueSetupError, so the worker does not start. O(1): one call.
        """
        try:
            response = self._client.get_queue_attributes(QueueUrl=self._url, AttributeNames=["RedrivePolicy"])
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error
        count = _max_receive_count(response.get("Attributes", {}).get("RedrivePolicy"))
        if count is None:
            raise QueueSetupError("queue_has_no_failure_bin")
        return count

    # --- Helpers -----------------------------------------------------------

    def _delete(self, receipt: str) -> None:
        try:
            self._client.delete_message(QueueUrl=self._url, ReceiptHandle=receipt)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _STALE_RECEIPT_CODES:
                raise LeaseLost from None
            raise BackendUnavailable("queue") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("queue") from error

    def _remember(self, receipt: str, body: str) -> None:
        self._bodies[receipt] = body
        while len(self._bodies) > _REMEMBERED_DELIVERIES:
            self._bodies.popitem(last=False)
