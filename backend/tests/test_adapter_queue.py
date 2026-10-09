"""SqsQueue against a stubbed boto3 client (botocore.stub.Stubber).

No network: Stubber checks each call's parameters and hands back a canned
answer. Connection failures come from a tiny client object that raises them.
"""

import json
import unittest
from typing import Any
from unittest import mock

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber

from kaagaz.adapters.queue import SqsQueue
from kaagaz.ingestion.errors import BackendUnavailable
from kaagaz.ingestion.ports import Job
from kaagaz.jobs.ports import LeaseLost, QueueSetupError
from kaagaz.jobs.worker import JobFailed, Worker
from tests.settings_factory import make_settings

URL = "http://localhost:9324/000000000000/kaagaz-jobs"
FAILED_URL = URL + "-failed"
FAILED_ARN = "arn:aws:sqs:us-east-1:000000000000:kaagaz-jobs-failed"
LEASE, WAIT = 60, 1
JOB = Job("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
BODY = json.dumps({"customer_id": JOB.customer_id, "document_id": JOB.document_id})

RECEIVE_PARAMS = {
    "QueueUrl": URL,
    "MaxNumberOfMessages": 1,
    "WaitTimeSeconds": WAIT,
    "VisibilityTimeout": LEASE,
    "MessageSystemAttributeNames": ["ApproximateReceiveCount"],
    "AttributeNames": ["ApproximateReceiveCount"],
}


def make_client() -> Any:
    return boto3.client(
        "sqs",
        region_name="us-east-1",
        endpoint_url="http://localhost:9324",
        # Stubber answers before a request is signed, so no keys are needed.
        config=Config(signature_version=UNSIGNED),
    )


def message(receipt: str = "r-1", body: str = BODY, count: str = "1") -> dict[str, Any]:
    return {
        "MessageId": "m-1",
        "ReceiptHandle": receipt,
        "Body": body,
        "Attributes": {"ApproximateReceiveCount": count},
    }


class StubbedQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        client = make_client()
        self.stub = Stubber(client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        settings = make_settings(queue_url=URL, queue_lease_seconds=LEASE, queue_wait_seconds=WAIT)
        self.queue = SqsQueue(settings, client=client)

    def tearDown(self) -> None:
        self.stub.assert_no_pending_responses()

    def receive_one(self, **fields: Any) -> None:
        self.stub.add_response("receive_message", {"Messages": [message(**fields)]}, RECEIVE_PARAMS)

    def test_enqueue_sends_ids_only(self) -> None:
        self.stub.add_response("send_message", {"MessageId": "m-1"}, {"QueueUrl": URL, "MessageBody": BODY})
        self.queue.enqueue(JOB)
        self.assertEqual(json.loads(BODY), {"customer_id": JOB.customer_id, "document_id": JOB.document_id})

    def test_receive_leases_one_job_with_its_attempt(self) -> None:
        self.receive_one(receipt="r-7", count="3")
        delivery = self.queue.receive()
        assert delivery is not None
        self.assertEqual((delivery.job, delivery.receipt, delivery.attempt), (JOB, "r-7", 3))

    def test_empty_long_poll_is_none(self) -> None:
        self.stub.add_response("receive_message", {}, RECEIVE_PARAMS)
        self.assertIsNone(self.queue.receive())

    def test_ack_deletes(self) -> None:
        self.receive_one()
        self.stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "r-1"})
        delivery = self.queue.receive()
        assert delivery is not None
        self.queue.ack(delivery.receipt)

    def test_extend_renews_the_lease(self) -> None:
        self.stub.add_response(
            "change_message_visibility",
            {},
            {"QueueUrl": URL, "ReceiptHandle": "r-1", "VisibilityTimeout": LEASE},
        )
        self.queue.extend("r-1")

    def test_defer_sends_the_job_again_with_a_delay_and_its_tries_then_deletes(self) -> None:
        """PRD 5 V1: the second receive of a job that had 1 try before (attempt 3) is
        handed back as a new message with tries 2, the attempts before this delivery,
        so its next delivery is attempt 3 again: the deferred one costs no attempt."""
        self.receive_one(receipt="r-3", body=json.dumps({**json.loads(BODY), "tries": 1}), count="2")
        self.stub.add_response(
            "change_message_visibility",
            {},
            {"QueueUrl": URL, "ReceiptHandle": "r-3", "VisibilityTimeout": LEASE},
        )
        again = json.dumps({**json.loads(BODY), "tries": 2})
        self.stub.add_response(
            "send_message", {"MessageId": "m-2"}, {"QueueUrl": URL, "MessageBody": again, "DelaySeconds": 30}
        )
        self.stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "r-3"})
        delivery = self.queue.receive()
        assert delivery is not None and delivery.attempt == 3
        self.queue.defer(delivery.receipt, 30)

        self.receive_one(receipt="r-4", body=again, count="1")
        delivery = self.queue.receive()
        assert delivery is not None
        self.assertEqual(delivery.attempt, 3)  # 2 earlier tries + this first receive

    def test_defer_with_a_lost_lease_sends_nothing(self) -> None:
        self.receive_one()
        self.stub.add_client_error(
            "change_message_visibility", "ReceiptHandleIsInvalid", http_status_code=400
        )
        delivery = self.queue.receive()
        assert delivery is not None
        with self.assertRaises(LeaseLost):
            self.queue.defer(delivery.receipt, 30)
        # No send_message and no delete_message were stubbed, so none was made.

    def test_defer_takes_0_to_15_minutes_and_a_known_receipt(self) -> None:
        self.receive_one()
        delivery = self.queue.receive()
        assert delivery is not None
        for seconds in (-1, 15 * 60 + 1):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.queue.defer(delivery.receipt, seconds)
        with self.assertRaises(ValueError):
            self.queue.defer("never-received", 30)

    def test_a_tries_that_is_not_0_to_1000_is_a_bad_message(self) -> None:
        for tries in (-1, 1001, "2", True, 1.5):
            with self.subTest(tries=tries):
                bad = json.dumps({**json.loads(BODY), "tries": tries})
                self.receive_one(receipt="r-bad", body=bad)
                self.stub.add_response(
                    "change_message_visibility",
                    {},
                    {"QueueUrl": URL, "ReceiptHandle": "r-bad", "VisibilityTimeout": LEASE},
                )
                self.stub.add_response(
                    "send_message",
                    {"MessageId": "m-3"},
                    {
                        "QueueUrl": FAILED_URL,
                        "MessageBody": bad,
                        "MessageAttributes": {
                            "failure_code": {"DataType": "String", "StringValue": "bad_message"}
                        },
                    },
                )
                self.stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "r-bad"})
                with self.assertLogs("kaagaz.adapters.queue", "WARNING"):
                    self.assertIsNone(self.queue.receive())

    def test_depth_counts_the_waiting_jobs_of_each_lane(self) -> None:
        def waiting(stub: Stubber, url: str, count: str) -> None:
            answer = {"Attributes": {"ApproximateNumberOfMessages": count}}
            params = {"QueueUrl": url, "AttributeNames": ["ApproximateNumberOfMessages"]}
            stub.add_response("get_queue_attributes", answer, params)

        waiting(self.stub, URL, "7")
        self.assertEqual(self.queue.depth(), {"express": 0, "main": 7})  # no express lane

        client = make_client()
        stub = Stubber(client)
        stub.activate()
        self.addCleanup(stub.deactivate)
        settings = make_settings(queue_url=URL, queue_express_url=URL + "-express", queue_wait_seconds=WAIT)
        waiting(stub, URL + "-express", "2")
        waiting(stub, URL, "0")
        self.assertEqual(SqsQueue(settings, client=client).depth(), {"express": 2, "main": 0})
        stub.assert_no_pending_responses()

    def test_stale_receipt_is_lease_lost(self) -> None:
        for code in ("ReceiptHandleIsInvalid", "AWS.SimpleQueueService.MessageNotInflight"):
            with self.subTest(code=code):
                self.stub.add_client_error("delete_message", code, http_status_code=400)
                with self.assertRaises(LeaseLost):
                    self.queue.ack("old")
                self.stub.add_client_error("change_message_visibility", code, http_status_code=400)
                with self.assertRaises(LeaseLost):
                    self.queue.extend("old")

    def test_bury_sends_the_job_and_its_code_to_the_failure_bin_then_deletes(self) -> None:
        self.receive_one()
        self.stub.add_response(
            "change_message_visibility",
            {},
            {"QueueUrl": URL, "ReceiptHandle": "r-1", "VisibilityTimeout": LEASE},
        )
        self.stub.add_response(
            "send_message",
            {"MessageId": "m-2"},
            {
                "QueueUrl": FAILED_URL,
                "MessageBody": BODY,
                "MessageAttributes": {"failure_code": {"DataType": "String", "StringValue": "corrupt_file"}},
            },
        )
        self.stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "r-1"})
        delivery = self.queue.receive()
        assert delivery is not None
        self.queue.bury(delivery.receipt, "corrupt_file")

    def test_bury_with_a_lost_lease_never_reaches_the_failure_bin(self) -> None:
        self.receive_one()
        self.stub.add_client_error(
            "change_message_visibility", "ReceiptHandleIsInvalid", http_status_code=400
        )
        delivery = self.queue.receive()
        assert delivery is not None
        with self.assertRaises(LeaseLost):
            self.queue.bury(delivery.receipt, "corrupt_file")
        # No send_message and no delete_message were stubbed, so none was made.

    def test_bury_of_an_unknown_receipt_is_a_bug(self) -> None:
        with self.assertRaises(ValueError):
            self.queue.bury("never-received", "corrupt_file")

    def test_message_that_is_not_a_job_is_buried_as_bad_message(self) -> None:
        for bad in (
            "not json",
            "[]",
            json.dumps({"customer_id": "c"}),
            json.dumps({"customer_id": 1, "document_id": 2}),
        ):
            with self.subTest(body=bad):
                self.receive_one(receipt="r-bad", body=bad)
                self.stub.add_response(
                    "change_message_visibility",
                    {},
                    {"QueueUrl": URL, "ReceiptHandle": "r-bad", "VisibilityTimeout": LEASE},
                )
                self.stub.add_response(
                    "send_message",
                    {"MessageId": "m-3"},
                    {
                        "QueueUrl": FAILED_URL,
                        "MessageBody": bad,
                        "MessageAttributes": {
                            "failure_code": {"DataType": "String", "StringValue": "bad_message"}
                        },
                    },
                )
                self.stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "r-bad"})
                with self.assertLogs("kaagaz.adapters.queue", "WARNING"):
                    self.assertIsNone(self.queue.receive())

    def test_queue_errors_are_backend_unavailable(self) -> None:
        self.stub.add_client_error("send_message", "InternalError", http_status_code=500)
        with self.assertRaises(BackendUnavailable):
            self.queue.enqueue(JOB)
        self.stub.add_client_error(
            "receive_message", "AWS.SimpleQueueService.NonExistentQueue", http_status_code=400
        )
        with self.assertRaises(BackendUnavailable):
            self.queue.receive()
        self.stub.add_client_error("delete_message", "InternalError", http_status_code=500)
        with self.assertRaises(BackendUnavailable):
            self.queue.ack("r-1")

    def test_ping(self) -> None:
        self.stub.add_response(
            "get_queue_attributes",
            {"Attributes": {"QueueArn": "arn:aws:sqs:us-east-1:000000000000:kaagaz-jobs"}},
            {"QueueUrl": URL, "AttributeNames": ["QueueArn"]},
        )
        self.queue.ping()
        self.stub.add_client_error("get_queue_attributes", "AWS.SimpleQueueService.NonExistentQueue")
        with self.assertRaises(BackendUnavailable):
            self.queue.ping()

    def redrive_policy(self, attributes: dict[str, str]) -> None:
        self.stub.add_response(
            "get_queue_attributes",
            {"Attributes": attributes},
            {"QueueUrl": URL, "AttributeNames": ["RedrivePolicy"]},
        )

    def test_max_receives_is_read_from_the_redrive_policy(self) -> None:
        # SQS writes maxReceiveCount as a number; some replies carry it as a string.
        for count in (5, "5"):
            with self.subTest(count=count):
                policy = {"deadLetterTargetArn": FAILED_ARN, "maxReceiveCount": count}
                self.redrive_policy({"RedrivePolicy": json.dumps(policy)})
                self.assertEqual(self.queue.max_receives(), 5)

    def test_a_queue_without_a_redrive_policy_refuses_to_start(self) -> None:
        for name, attributes in {
            "no redrive policy": {},
            "no maxReceiveCount": {"RedrivePolicy": json.dumps({"deadLetterTargetArn": FAILED_ARN})},
            "not json": {"RedrivePolicy": "not json"},
            "not an object": {"RedrivePolicy": "[5]"},
            "not a number": {"RedrivePolicy": json.dumps({"maxReceiveCount": "five"})},
            "zero": {"RedrivePolicy": json.dumps({"maxReceiveCount": 0})},
            "not whole": {"RedrivePolicy": json.dumps({"maxReceiveCount": 5.5})},
            "a boolean": {"RedrivePolicy": json.dumps({"maxReceiveCount": True})},
        }.items():
            with self.subTest(name):
                self.redrive_policy(attributes)
                with self.assertRaises(QueueSetupError) as ctx:
                    self.queue.max_receives()
                self.assertEqual(ctx.exception.code, "queue_has_no_failure_bin")

    def test_worker_buries_the_last_delivery_as_retries_exhausted_through_the_adapter(self) -> None:
        """The real Worker against this adapter: the delivery after the last attempt
        marks the document, buries the job with its code, and does not run the handler."""
        self.receive_one(receipt="r-5", count="6")
        self.stub.add_response(
            "change_message_visibility",
            {},
            {"QueueUrl": URL, "ReceiptHandle": "r-5", "VisibilityTimeout": LEASE},
        )
        self.stub.add_response(
            "send_message",
            {"MessageId": "m-5"},
            {
                "QueueUrl": FAILED_URL,
                "MessageBody": BODY,
                "MessageAttributes": {
                    "failure_code": {"DataType": "String", "StringValue": "retries_exhausted"}
                },
            },
        )
        self.stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "r-5"})
        marked: list[Job] = []
        ran: list[Job] = []
        worker = Worker(
            self.queue, lambda job, beat: ran.append(job), max_attempts=5, on_exhausted=marked.append
        )

        self.assertTrue(worker.run_once())
        self.assertEqual((marked, ran), ([JOB], []))

    def test_worker_buries_a_permanent_failure_through_the_adapter(self) -> None:
        """The real Worker against this adapter: a non-retryable failure is buried."""
        self.receive_one(receipt="r-9")
        self.stub.add_response(
            "change_message_visibility",
            {},
            {"QueueUrl": URL, "ReceiptHandle": "r-9", "VisibilityTimeout": LEASE},
        )
        self.stub.add_response(
            "send_message",
            {"MessageId": "m-4"},
            {
                "QueueUrl": FAILED_URL,
                "MessageBody": BODY,
                "MessageAttributes": {
                    "failure_code": {"DataType": "String", "StringValue": "too_many_pages"}
                },
            },
        )
        self.stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "r-9"})

        def handler(job: Job, heartbeat: Any) -> None:
            raise JobFailed("too_many_pages", retryable=False)

        marked: list[Job] = []
        self.assertTrue(Worker(self.queue, handler, max_attempts=5, on_exhausted=marked.append).run_once())
        self.assertEqual(marked, [])  # a failure for good keeps its own code


EXPRESS_URL = URL + "-express"


class ExpressLaneTest(unittest.TestCase):
    """PRD 5 Q4: two lanes, the express one asked first without waiting; each receipt's
    ack, extend, bury and defer go to the lane it came from, with its own failure bin."""

    def setUp(self) -> None:
        client = make_client()
        self.stub = Stubber(client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        settings = make_settings(
            queue_url=URL, queue_express_url=EXPRESS_URL, queue_lease_seconds=LEASE, queue_wait_seconds=WAIT
        )
        self.queue = SqsQueue(settings, client=client)

    def tearDown(self) -> None:
        self.stub.assert_no_pending_responses()

    def receive(self, url: str, wait: int, messages: list[dict[str, Any]]) -> None:
        params = {**RECEIVE_PARAMS, "QueueUrl": url, "WaitTimeSeconds": wait}
        self.stub.add_response("receive_message", {"Messages": messages} if messages else {}, params)

    def test_enqueue_sends_to_the_lane_asked_for(self) -> None:
        self.stub.add_response(
            "send_message", {"MessageId": "m-1"}, {"QueueUrl": EXPRESS_URL, "MessageBody": BODY}
        )
        self.stub.add_response("send_message", {"MessageId": "m-2"}, {"QueueUrl": URL, "MessageBody": BODY})
        self.queue.enqueue(JOB, express=True)
        self.queue.enqueue(JOB)

    def test_one_lane_sends_express_jobs_to_the_main_queue(self) -> None:
        client = make_client()
        stub = Stubber(client)
        stub.activate()
        self.addCleanup(stub.deactivate)
        stub.add_response("send_message", {"MessageId": "m-1"}, {"QueueUrl": URL, "MessageBody": BODY})
        SqsQueue(make_settings(queue_url=URL), client=client).enqueue(JOB, express=True)
        stub.assert_no_pending_responses()

    def test_receive_asks_express_without_waiting_then_main_with_the_long_poll(self) -> None:
        self.receive(EXPRESS_URL, 0, [message(receipt="r-e")])
        delivery = self.queue.receive()
        assert delivery is not None and delivery.receipt == "r-e"

        self.receive(EXPRESS_URL, 0, [])
        self.receive(URL, WAIT, [message(receipt="r-m")])
        delivery = self.queue.receive()
        assert delivery is not None and delivery.receipt == "r-m"

        self.receive(EXPRESS_URL, 0, [])
        self.receive(URL, WAIT, [])
        self.assertIsNone(self.queue.receive())

    def test_an_express_receipt_settles_in_the_express_lane(self) -> None:
        self.receive(EXPRESS_URL, 0, [message(receipt="r-1")])
        lease = {"QueueUrl": EXPRESS_URL, "ReceiptHandle": "r-1", "VisibilityTimeout": LEASE}
        self.stub.add_response("change_message_visibility", {}, lease)
        self.stub.add_response("delete_message", {}, {"QueueUrl": EXPRESS_URL, "ReceiptHandle": "r-1"})
        delivery = self.queue.receive()
        assert delivery is not None
        self.queue.extend(delivery.receipt)
        self.queue.ack(delivery.receipt)

        self.receive(EXPRESS_URL, 0, [message(receipt="r-2")])
        self.stub.add_response("change_message_visibility", {}, {**lease, "ReceiptHandle": "r-2"})
        self.stub.add_response(
            "send_message",
            {"MessageId": "m-2"},
            {
                "QueueUrl": EXPRESS_URL + "-failed",
                "MessageBody": BODY,
                "MessageAttributes": {"failure_code": {"DataType": "String", "StringValue": "corrupt_file"}},
            },
        )
        self.stub.add_response("delete_message", {}, {"QueueUrl": EXPRESS_URL, "ReceiptHandle": "r-2"})
        delivery = self.queue.receive()
        assert delivery is not None
        self.queue.bury(delivery.receipt, "corrupt_file")

        self.receive(EXPRESS_URL, 0, [message(receipt="r-3")])
        self.stub.add_response("change_message_visibility", {}, {**lease, "ReceiptHandle": "r-3"})
        self.stub.add_response(
            "send_message",
            {"MessageId": "m-3"},
            {"QueueUrl": EXPRESS_URL, "MessageBody": BODY, "DelaySeconds": 300},
        )
        self.stub.add_response("delete_message", {}, {"QueueUrl": EXPRESS_URL, "ReceiptHandle": "r-3"})
        delivery = self.queue.receive()
        assert delivery is not None
        self.queue.defer(delivery.receipt, 300)  # first receive, no earlier tries: tries stays 0

    def defer_an_express_job(self) -> None:
        self.receive(EXPRESS_URL, 0, [message(receipt="r-e")])
        lease = {"QueueUrl": EXPRESS_URL, "ReceiptHandle": "r-e", "VisibilityTimeout": LEASE}
        self.stub.add_response("change_message_visibility", {}, lease)
        again = {"QueueUrl": EXPRESS_URL, "MessageBody": BODY, "DelaySeconds": 30}
        self.stub.add_response("send_message", {"MessageId": "m-2"}, again)
        self.stub.add_response("delete_message", {}, {"QueueUrl": EXPRESS_URL, "ReceiptHandle": "r-e"})
        delivery = self.queue.receive()
        assert delivery is not None
        self.queue.defer(delivery.receipt, 30)

    def test_after_a_defer_the_next_receive_asks_the_main_lane_first_once(self) -> None:
        """A customer at its fair share with express jobs would otherwise have each one
        deferred and the next express one taken, so the main lane was never asked."""
        self.defer_an_express_job()

        self.receive(URL, 0, [message(receipt="r-m")])  # the main lane first, without waiting
        delivery = self.queue.receive()
        assert delivery is not None and delivery.receipt == "r-m"

        self.receive(EXPRESS_URL, 0, [message(receipt="r-e2")])  # once only: express first again
        delivery = self.queue.receive()
        assert delivery is not None and delivery.receipt == "r-e2"

    def test_after_a_defer_an_empty_main_lane_falls_back_to_the_usual_order(self) -> None:
        self.defer_an_express_job()

        self.receive(URL, 0, [])
        self.receive(EXPRESS_URL, 0, [message(receipt="r-e2")])
        delivery = self.queue.receive()
        assert delivery is not None and delivery.receipt == "r-e2"

    def test_max_receives_reads_both_lanes_and_takes_the_smaller(self) -> None:
        for url, count in ((EXPRESS_URL, 8), (URL, 10)):
            self.stub.add_response(
                "get_queue_attributes",
                {"Attributes": {"RedrivePolicy": json.dumps({"maxReceiveCount": count})}},
                {"QueueUrl": url, "AttributeNames": ["RedrivePolicy"]},
            )
        self.assertEqual(self.queue.max_receives(), 8)
        self.stub.add_response(
            "get_queue_attributes",
            {"Attributes": {}},
            {"QueueUrl": EXPRESS_URL, "AttributeNames": ["RedrivePolicy"]},
        )
        with self.assertRaises(QueueSetupError):
            self.queue.max_receives()  # an express lane with no failure bin stops the worker


class QueueSettingsTest(unittest.TestCase):
    def test_wait_and_lease_must_be_within_sqs_limits(self) -> None:
        for overrides in (
            {"queue_wait_seconds": 0},
            {"queue_wait_seconds": 21},
            {"queue_lease_seconds": 0},
            {"queue_lease_seconds": 12 * 60 * 60 + 1},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                SqsQueue(make_settings(**overrides), client=make_client())


class _UnreachableClient:
    def __getattr__(self, name: str) -> Any:
        def call(**kwargs: Any) -> Any:
            raise EndpointConnectionError(endpoint_url="http://localhost:9324")

        return call


class UnreachableQueueTest(unittest.TestCase):
    def test_every_call_maps_to_backend_unavailable(self) -> None:
        queue = SqsQueue(make_settings(), client=_UnreachableClient())
        calls = {
            "enqueue": lambda: queue.enqueue(JOB),
            "receive": queue.receive,
            "ack": lambda: queue.ack("r-1"),
            "extend": lambda: queue.extend("r-1"),
            "ping": queue.ping,
            "max_receives": queue.max_receives,
            "depth": queue.depth,
        }
        for name, call in calls.items():
            with self.subTest(call=name), self.assertRaises(BackendUnavailable):
                call()


class ClientConfigTest(unittest.TestCase):
    """AR15b: the SQS client gives up fast, leaving room for the long poll."""

    def test_the_client_has_short_timeouts_and_two_retries(self) -> None:
        queue = SqsQueue(make_settings(queue_wait_seconds=20))  # builds the real client; nothing is sent
        config = queue._client.meta.config
        self.assertEqual((config.connect_timeout, config.read_timeout), (5, 30))
        self.assertEqual(
            config.retries["total_max_attempts"], 3
        )  # max_attempts 2 is two retries after the first try

    def test_sends_use_their_own_client_with_a_short_read_timeout(self) -> None:
        made: list[_RecordingClient] = []

        def client(service_name: str, **kwargs: Any) -> _RecordingClient:
            made.append(_RecordingClient(_real_boto3_client(service_name, **kwargs).meta.config))
            return made[-1]

        with mock.patch.object(boto3, "client", client):
            queue = SqsQueue(make_settings(queue_url=URL, queue_wait_seconds=20))
        queue.enqueue(JOB)
        self.assertIsNone(queue.receive())  # a bad message, buried at once: one more send
        (sender,) = [made_client for made_client in made if "send_message" in made_client.calls]
        (receiver,) = [made_client for made_client in made if "receive_message" in made_client.calls]
        self.assertIsNot(sender, receiver)
        self.assertEqual(sender.calls, ["send_message", "send_message"])
        self.assertEqual((sender.config.connect_timeout, sender.config.read_timeout), (5, 5))
        self.assertEqual(sender.config.retries["total_max_attempts"], 3)
        self.assertGreater(receiver.config.read_timeout, 20)  # longer than the long poll


_real_boto3_client = boto3.client


class _RecordingClient:
    """Stands in for one SQS client: keeps its real config and the calls made on it."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        def call(**kwargs: Any) -> Any:
            self.calls.append(name)
            return {"Messages": [message(body="not a job")]} if name == "receive_message" else {}

        return call


if __name__ == "__main__":
    unittest.main()
