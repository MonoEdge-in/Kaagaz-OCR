"""The Wake link: AWS Lambda kaagaz-wake behind a Function URL (EC2 PRD, WP-G and section 8).

Opening https://<function-url>/?key=<WAKE_TOKEN> starts the stopped EC2 server. A wrong or
missing key gets a bare 404 and makes no AWS call. INSTANCE_ID and WAKE_TOKEN are set in
the Lambda console, never in git. Python 3.12, with only the boto3 the runtime ships.
"""

import hmac
import html
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

STARTING = "Starting. The site opens in about 3 minutes, and documents are read after about 10."
MESSAGES = {
    "pending": "Already starting. The site opens in a few minutes.",
    "running": "Already running.",
    "stopping": "The server is stopping. Open this link again in a minute to start it.",
}
NOT_STARTED = "The server could not be started just now. Try again in a few minutes."
HEADERS = {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


def page(status: int, text: str) -> dict[str, Any]:
    body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Kaagaz</title></head><body><p>{html.escape(text)}</p></body></html>"
    )
    return {"statusCode": status, "headers": HEADERS, "body": body}


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    key = (event.get("queryStringParameters") or {}).get("key", "")
    token = os.environ.get("WAKE_TOKEN", "")
    if not token or not key or not hmac.compare_digest(key.encode(), token.encode()):
        return page(404, "Not found.")
    return wake(boto3.client("ec2"), os.environ["INSTANCE_ID"])


def wake(ec2: Any, instance_id: str) -> dict[str, Any]:
    reply = ec2.describe_instances(InstanceIds=[instance_id])
    state = str(reply["Reservations"][0]["Instances"][0]["State"]["Name"])
    if state != "stopped":
        return page(200, MESSAGES.get(state, f"The server is {state}. Ask your administrator."))
    try:
        ec2.start_instances(InstanceIds=[instance_id])
    except ClientError as error:
        logging.getLogger(__name__).warning("start_instances failed: %s", error.response["Error"]["Code"])
        return page(200, NOT_STARTED)
    return page(200, STARTING)
