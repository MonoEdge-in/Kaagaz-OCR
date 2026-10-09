"""The Wake link's Lambda (infra/aws/wake/handler.py, EC2 PRD WP-G) against a stubbed EC2 client.

The handler lives outside the backend package, so it is loaded from its path. No network:
botocore.stub.Stubber checks each call's parameters and hands back a canned answer.
"""

import importlib.util
import os
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.stub import Stubber

HANDLER = Path(__file__).resolve().parents[2] / "infra" / "aws" / "wake" / "handler.py"
_spec = importlib.util.spec_from_file_location("kaagaz_wake_handler", HANDLER)
assert _spec is not None and _spec.loader is not None
wake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wake)

INSTANCE = "i-0123456789abcdef0"
TOKEN = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a6978"
IDS = {"InstanceIds": [INSTANCE]}
CODES = {"pending": 0, "running": 16, "stopping": 64, "stopped": 80}


def described(state: str) -> dict[str, Any]:
    instance = {"InstanceId": INSTANCE, "State": {"Code": CODES[state], "Name": state}}
    return {"Reservations": [{"Instances": [instance]}]}


def event(key: str | None) -> dict[str, Any]:
    # A Function URL event has no queryStringParameters at all when the URL has no query.
    return {"rawPath": "/"} if key is None else {"rawPath": "/", "queryStringParameters": {"key": key}}


class WakeHandlerTest(unittest.TestCase):
    def setUp(self) -> None:
        client = boto3.client(
            "ec2",
            region_name="ap-south-1",
            # Stubber answers before a request is signed, so no keys are needed.
            config=Config(signature_version=UNSIGNED),
        )
        self.stub = Stubber(client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.make_client = mock.Mock(return_value=client)
        for patcher in (
            mock.patch.dict(os.environ, {"INSTANCE_ID": INSTANCE, "WAKE_TOKEN": TOKEN}),
            mock.patch.object(wake.boto3, "client", self.make_client),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.stub.assert_no_pending_responses()

    def call(self, key: str | None) -> dict[str, Any]:
        response: dict[str, Any] = wake.handler(event(key), None)
        self.assertEqual(
            response["headers"], {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}
        )
        self.assertNotIn(TOKEN, response["body"])  # the key is never echoed
        return response

    def assert_not_found(self, key: str | None) -> None:
        response = self.call(key)
        self.assertEqual(
            (response["statusCode"], response["body"]), (404, wake.page(404, "Not found.")["body"])
        )
        self.make_client.assert_not_called()  # no EC2 client, so no AWS call

    def test_a_wrong_key_gets_404_and_makes_no_aws_call(self) -> None:
        self.assert_not_found(TOKEN[:-1] + "1")
        self.assert_not_found(TOKEN.upper())
        self.assert_not_found("caf\u00e9")  # not ASCII: still a plain 404, not an error

    def test_a_missing_key_gets_404(self) -> None:
        self.assert_not_found(None)
        self.assert_not_found("")
        response = wake.handler({"queryStringParameters": {"other": TOKEN}}, None)
        self.assertEqual(response["statusCode"], 404)

    def test_no_token_in_the_environment_refuses_every_key(self) -> None:
        with mock.patch.dict(os.environ, {"WAKE_TOKEN": ""}):
            self.assert_not_found("")
            self.assert_not_found(TOKEN)

    def test_a_stopped_instance_is_started(self) -> None:
        self.stub.add_response("describe_instances", described("stopped"), IDS)
        starting = {"InstanceId": INSTANCE, "CurrentState": {"Code": 0, "Name": "pending"}}
        self.stub.add_response("start_instances", {"StartingInstances": [starting]}, IDS)
        response = self.call(TOKEN)
        self.assertEqual(response["statusCode"], 200)
        self.assertIn(
            "<p>Starting. The site opens in about 3 minutes.</p>",
            response["body"],
        )
        self.make_client.assert_called_once_with("ec2")

    def test_a_running_instance_is_not_started(self) -> None:
        self.stub.add_response("describe_instances", described("running"), IDS)
        response = self.call(TOKEN)  # a start_instances call would find no stubbed answer and fail
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("Already running.", response["body"])

    def test_a_pending_or_stopping_instance_is_not_started(self) -> None:
        for state, text in (("pending", "Already starting."), ("stopping", "The server is stopping.")):
            self.stub.add_response("describe_instances", described(state), IDS)
            self.assertIn(text, self.call(TOKEN)["body"])

    def test_a_refused_start_says_try_again(self) -> None:
        self.stub.add_response("describe_instances", described("stopped"), IDS)
        self.stub.add_client_error("start_instances", "InsufficientInstanceCapacity", expected_params=IDS)
        with self.assertLogs(level="WARNING") as logs:
            response = self.call(TOKEN)
        self.assertIn("could not be started just now", response["body"])
        self.assertEqual(
            logs.output, ["WARNING:kaagaz_wake_handler:start_instances failed: InsufficientInstanceCapacity"]
        )

    def test_the_page_escapes_its_text(self) -> None:
        body = wake.page(200, '<script>alert("x")</script>')["body"]
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", body)
