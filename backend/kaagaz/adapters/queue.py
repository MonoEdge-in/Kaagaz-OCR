"""The job queue over the SQS API: ElasticMQ on the laptop, Amazon SQS in AWS.

It implements ``JobQueue`` (upload puts a job in) and ``WorkQueue`` (the
worker takes it out), with the lease model described in kaagaz/jobs/ports.py:

* The message body is ``{"customer_id": ..., "document_id": ...}``: ids only,
  never a file or its name. A job handed back by ``defer`` also carries
  ``"tries"``: the attempts it used before, a whole number 0 to MAX_TRIES (1000).
* Two lanes (PRD 5 Q4): ``enqueue(job, express=True)`` sends the job to
  ``queue_express_url``, the express lane; any other job, or every job when there
  is no express lane, goes to ``queue_url``. The caller decides ``express`` from
  the server's own page count (kaagaz/ingestion/pagecount.py).
* ``receive`` asks the express lane first without waiting, then long-polls the
  main lane for up to ``queue_wait_seconds``, and hides the job for
  ``queue_lease_seconds``. Right after a ``defer`` it asks the main lane first,
  without waiting, once: a customer at its fair share whose jobs fill the express
  lane would otherwise have one deferred after another while the main lane is never
  asked. The attempt number is ``tries`` plus SQS's own receive
  count, so it counts crashed deliveries too. Each receipt remembers its lane and
  body, so ``ack``, ``extend``, ``bury`` and ``defer`` go to the lane it came from.
* ``ack`` deletes the message; ``extend`` resets its lease.
* ``defer`` hands the job back without counting an attempt (PRD 5 Q5, V1): it
  sends the same job to its lane again with DelaySeconds and ``tries`` set to the
  attempts used before this delivery, then deletes the delivery. A visibility
  change would not do: SQS and ElasticMQ count every receive, so the redrive
  policy would move a job deferred ten times to the failure bin. It first renews
  the lease, as ``bury`` does, so a stale delivery sends nothing.
* ``depth`` counts the jobs waiting in each lane, for the operator's Health
  screen (PRD 5 U9).
* ``bury`` puts the job in its lane's failure bin (the lane's URL + ``"-failed"``)
  with its code, then deletes it. It first renews the lease, so a worker whose
  lease already ran out cannot put a job in the failure bin that another worker
  is still running.
* After the maximum number of receives, the queue itself moves a job to the
  failure bin: that is the redrive policy in infra/elasticmq/elasticmq.conf
  (and in the AWS queue's settings), not code here. ``max_receives`` reads
  that maximum from each lane, so the worker knows a job's last delivery.
* A stale receipt raises ``LeaseLost``. Any other queue failure is
  ``BackendUnavailable``.

A message that is not a job this code wrote (bad JSON, missing ids, a ``tries``
that is not a whole number 0 to MAX_TRIES) is buried at once with the code
``bad_message``, so it cannot block the queue.

PROVISIONAL: the lease length, the long-poll wait and the maximum receives
are Vrushit's queue settings (PRD section 6).
"""

import contextlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
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
_MAX_DELAY_SECONDS = 15 * 60

# The most attempts a handed-back job's body may carry (PRD 5 V1): far above any
# JOB_MAX_ATTEMPTS, so it only bounds what a message can claim.
MAX_TRIES = 1000

# bury and defer need the message body and its lane, and only have the receipt,
# so those of recent deliveries are kept here. The worker runs
# one job at a time, so only the latest is ever needed; a few more cover jobs
# left to time out (a retryable failure never calls ack or bury).
_REMEMBERED_DELIVERIES = 16


# AR15b: a queue that does not answer fails fast: 5 s to connect, and at most two
# retries (botocore's max_attempts counts retries: three tries in all), so a "read
# again" or an upload never hangs on the queue. Two clients:
# * sends (an upload, "read again", the failure bin) and the Health screen's depth
#   are one short request each, so their client reads for 5 s: about 30 s per call
#   at worst, not 105 s (at QUEUE_WAIT_SECONDS=20);
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


@dataclass(frozen=True, slots=True)
class _Received:
    """What a receipt remembers: its lane, its body, and its job and attempt (None
    and 0 for a message that is not a job)."""

    url: str
    body: str
    job: Job | None
    attempt: int


def _body(job: Job, tries: int = 0) -> str:
    fields: dict[str, object] = {"customer_id": job.customer_id, "document_id": job.document_id}
    if tries:
        fields["tries"] = tries
    return json.dumps(fields)


def _job_from_body(body: str) -> tuple[Job, int] | None:
    """The job and the attempts it used before (``tries``, 0 when absent), or None
    for a body this code did not write."""
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
    tries = data.get("tries", 0)
    if not isinstance(tries, int) or isinstance(tries, bool) or not 0 <= tries <= MAX_TRIES:
        return None
    return Job(customer_id=customer_id, document_id=document_id), tries


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
        self._express_url = settings.queue_express_url
        self._wait = settings.queue_wait_seconds
        self._lease = settings.queue_lease_seconds
        self._received: OrderedDict[str, _Received] = OrderedDict()
        self._main_first = False  # set by defer, for the next receive only

    # --- JobQueue ----------------------------------------------------------

    def enqueue(self, job: Job, *, express: bool = False) -> None:
        url = self._express_url if express and self._express_url is not None else self._url
        try:
            self._send_client.send_message(QueueUrl=url, MessageBody=_body(job))
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error

    # --- WorkQueue ---------------------------------------------------------

    def receive(self) -> Delivery | None:
        """One job, leased to this worker, or None if none came within the wait: the
        express lane first, without waiting, then the main lane's long poll; right
        after a ``defer``, the main lane without waiting before both. At most three
        calls."""
        url, message = self._url, None
        if self._main_first and self._express_url is not None:
            message = self._receive_from(self._url, 0)
        self._main_first = False
        if message is None and self._express_url is not None:
            url, message = self._express_url, self._receive_from(self._express_url, 0)
        if message is None:
            url, message = self._url, self._receive_from(self._url, self._wait)
        if message is None:
            return None
        receipt = str(message["ReceiptHandle"])
        body = str(message.get("Body", ""))
        parsed = _job_from_body(body)
        if parsed is None:
            self._remember(receipt, _Received(url, body, None, 0))
            logger.warning("queue message is not a job", extra={"code": "bad_message"})
            # LeaseLost: another delivery of the same message is burying it.
            with contextlib.suppress(LeaseLost):
                self.bury(receipt, "bad_message")
            return None
        job, tries = parsed
        # Only the queue's redrive policy acts on the count, so a queue that
        # leaves it out cannot change what happens to the job.
        attempt = tries + int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        self._remember(receipt, _Received(url, body, job, attempt))
        return Delivery(job=job, receipt=receipt, attempt=attempt)

    def ack(self, receipt: str) -> None:
        self._delete(receipt)
        self._received.pop(receipt, None)

    def extend(self, receipt: str) -> None:
        try:
            self._client.change_message_visibility(
                QueueUrl=self._lane(receipt), ReceiptHandle=receipt, VisibilityTimeout=self._lease
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _STALE_RECEIPT_CODES:
                raise LeaseLost from None
            raise BackendUnavailable("queue") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("queue") from error

    def defer(self, receipt: str, seconds: int) -> None:
        """Hand the job back, to be handed out again after ``seconds`` (0 to SQS's
        longest delay, 15 minutes), without counting this delivery as an attempt
        (the module docstring). Raises LeaseLost, and sends nothing, when another
        delivery owns the job. 3 calls."""
        if not 0 <= seconds <= _MAX_DELAY_SECONDS:
            raise ValueError(f"seconds must be 0 to {_MAX_DELAY_SECONDS}")
        received = self._received.get(receipt)
        if received is None or received.job is None:
            # Not a job's receipt from this queue object: a bug, never a normal case.
            raise ValueError("unknown receipt")
        self.extend(receipt)
        body = _body(received.job, min(received.attempt - 1, MAX_TRIES))
        try:
            self._send_client.send_message(QueueUrl=received.url, MessageBody=body, DelaySeconds=seconds)
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error
        self._delete(receipt)
        self._received.pop(receipt, None)
        self._main_first = True

    def bury(self, receipt: str, code: str) -> None:
        received = self._received.get(receipt)
        if received is None:
            # Not a receipt from this queue object: a bug, never a normal case.
            raise ValueError("unknown receipt")
        self.extend(receipt)  # LeaseLost here: another delivery owns the job
        try:
            self._send_client.send_message(
                QueueUrl=received.url + "-failed",
                MessageBody=received.body,
                MessageAttributes={"failure_code": {"DataType": "String", "StringValue": code}},
            )
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error
        self._delete(receipt)
        self._received.pop(receipt, None)

    # --- Health ------------------------------------------------------------

    def ping(self) -> None:
        """Raise BackendUnavailable unless the queue answers."""
        try:
            self._client.get_queue_attributes(QueueUrl=self._url, AttributeNames=["QueueArn"])
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error

    def depth(self) -> dict[str, int]:
        """The jobs waiting in each lane, ``{"express": n, "main": n}``: SQS's
        approximate count of visible messages, not counting leased ones. 0 for
        express when there is no express lane. One call a lane, on the short-timeout
        client."""
        express = 0 if self._express_url is None else self._waiting(self._express_url)
        return {"express": express, "main": self._waiting(self._url)}

    def _waiting(self, url: str) -> int:
        # The short-timeout client: the Health screen waits on this, never a long poll's time.
        try:
            response = self._send_client.get_queue_attributes(
                QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
            )
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error
        return int(response.get("Attributes", {}).get("ApproximateNumberOfMessages", "0"))

    # --- Settings ----------------------------------------------------------

    def max_receives(self) -> int:
        """The queue's maximum receives: ``maxReceiveCount`` in each lane's redrive
        policy, the smaller one (PRD 5 Q4: both lanes have a failure bin).

        The worker reads it once at start-up, to know which delivery of a job is
        the last (kaagaz/jobs/worker.py). SQS gives the count as a number or as a
        string, so both are read. A lane without a redrive policy is refused
        with QueueSetupError, so the worker does not start. One call a lane.
        """
        lanes = [self._url] if self._express_url is None else [self._express_url, self._url]
        return min(self._max_receives_of(url) for url in lanes)

    def _max_receives_of(self, url: str) -> int:
        try:
            response = self._client.get_queue_attributes(QueueUrl=url, AttributeNames=["RedrivePolicy"])
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error
        count = _max_receive_count(response.get("Attributes", {}).get("RedrivePolicy"))
        if count is None:
            raise QueueSetupError("queue_has_no_failure_bin")
        return count

    # --- Helpers -----------------------------------------------------------

    def _receive_from(self, url: str, wait: int) -> dict[str, Any] | None:
        try:
            response = self._client.receive_message(
                QueueUrl=url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=wait,
                VisibilityTimeout=self._lease,
                # Both names ask for the receive count: the new one for SQS,
                # the older one for queues that only know it (ElasticMQ).
                MessageSystemAttributeNames=["ApproximateReceiveCount"],
                AttributeNames=["ApproximateReceiveCount"],
            )
        except _ANY_ERROR as error:
            raise BackendUnavailable("queue") from error
        messages: list[dict[str, Any]] = response.get("Messages", [])
        return messages[0] if messages else None

    def _lane(self, receipt: str) -> str:
        """The lane ``receipt`` came from; the main lane for one it does not remember."""
        received = self._received.get(receipt)
        return self._url if received is None else received.url

    def _delete(self, receipt: str) -> None:
        try:
            self._client.delete_message(QueueUrl=self._lane(receipt), ReceiptHandle=receipt)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _STALE_RECEIPT_CODES:
                raise LeaseLost from None
            raise BackendUnavailable("queue") from error
        except _UNREACHABLE as error:
            raise BackendUnavailable("queue") from error

    def _remember(self, receipt: str, received: _Received) -> None:
        self._received[receipt] = received
        while len(self._received) > _REMEMBERED_DELIVERIES:
            self._received.popitem(last=False)
