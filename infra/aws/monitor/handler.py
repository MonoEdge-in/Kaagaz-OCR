"""Uptime, certificate, backup and schedule alerts: AWS Lambda kaagaz-monitor, run every 5 minutes
by an EventBridge schedule (#480, 3 Nov 2026).

Outside pingers would alarm every night, because the server stops itself when idle, so this
checks only what should hold right now:

* ``api``, ``site``, ``certificate``: once the server has run 10 minutes, /api/health and / answer
  2xx over HTTPS, and the certificate has 14 days or more left;
* ``backup``: once it has run an hour (a backup missed while it was stopped runs at boot), the
  newest ``<run>/done`` in the backup bucket (infra/ec2/pgdump.sh, one folder per run) is under
  26 hours old;
* ``schedule``: on weekdays from 09:45 to 10:00 IST, the server is running or was started today
  at or after 09:30 IST (the weekday schedule; by then it may have stopped itself again).

A check that fails twice in a row emails once through SNS; when it passes again, one more email
says so. The counts live in one SSM parameter (STATE_PARAMETER), written only when they change,
and only after the email went, so a failed one is sent again on the next round.
A check that does not run (the server is stopped) is forgotten. INSTANCE_ID, SITE_DOMAIN,
BACKUP_BUCKET and TOPIC_ARN are set in the Lambda console, never in git. Python 3.12, with only
the standard library and the boto3 the runtime ships, as infra/aws/wake/handler.py.
"""

import http.client
import json
import logging
import os
import socket
import ssl
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

STATE_PARAMETER = "/kaagaz/monitor/failures"
FAILURES_TO_ALERT = 2
SETTLE = timedelta(minutes=10)
BACKUP_SETTLE = timedelta(hours=1)
BACKUP_MAX_AGE = timedelta(hours=26)
CERT_MIN_DAYS = 14
TIMEOUT_SECONDS = 10
IST = timezone(timedelta(hours=5, minutes=30))  # India has no daylight saving
SCHEDULE_START = time(9, 30)
SCHEDULE_CHECK = (time(9, 45), time(10, 0))

# What each check's failure means, in plain words, then why.
PROBLEMS = {
    "api": "The API health page does not answer",
    "site": "The website does not answer",
    "certificate": "The HTTPS certificate expires soon",
    "backup": "No finished backup in the last 26 hours",
    "schedule": "The weekday 09:30 schedule did not start the server",
}

log = logging.getLogger(__name__)


def fetch(url: str) -> None:
    """Raises unless ``url`` answers 2xx (urllib raises on 4xx and 5xx, and follows redirects)."""
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS):  # noqa: S310 - fixed https URLs
        pass


def cert_days_left(domain: str, now: datetime) -> float:
    """Days until the certificate ``domain`` serves on 443 expires. An invalid one raises."""
    context = ssl.create_default_context()
    with (
        socket.create_connection((domain, 443), timeout=TIMEOUT_SECONDS) as raw,
        context.wrap_socket(raw, server_hostname=domain) as tls,
    ):
        cert = tls.getpeercert()  # verified against the system's trusted roots and the name
    if not cert:
        raise ValueError("no certificate")
    expires = ssl.cert_time_to_seconds(str(cert["notAfter"]))
    return (expires - now.timestamp()) / 86400


def reason(error: Exception) -> str:
    """A short why: the HTTP status or the error's name, never a body or a secret."""
    code = getattr(error, "code", None)
    if isinstance(code, int):
        return f"HTTP {code}"
    inner = getattr(error, "reason", None)  # URLError wraps the socket or TLS error
    return type(inner if isinstance(inner, Exception) else error).__name__


# Every way a request can fail: URLError, HTTPError, TLS and socket errors are OSErrors.
FAILED = (OSError, ValueError, http.client.HTTPException)


def newest_done(s3: Any, bucket: str) -> datetime | None:
    """When the newest ``<run>/done`` was written. The bucket keeps 35 days (a few hundred keys)."""
    newest = None
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            if str(item["Key"]).endswith("/done") and (newest is None or item["LastModified"] > newest):
                newest = item["LastModified"]
    return newest


def checks(
    instance: dict[str, Any],
    now: datetime,
    domain: str,
    last_backup: Callable[[], datetime | None],
    get: Callable[[str], None] | None = None,
    days_left: Callable[[str, datetime], float] | None = None,
) -> dict[str, str | None]:
    """Each check that applies now: None when it passed, else why it failed. Tests pass the
    probes; otherwise ``fetch`` and ``cert_days_left``."""
    get = get or fetch
    days_left = days_left or cert_days_left
    results: dict[str, str | None] = {}
    running = instance["State"]["Name"] == "running"
    launched: datetime = instance["LaunchTime"]
    local = now.astimezone(IST)
    if local.weekday() < 5 and SCHEDULE_CHECK[0] <= local.time() < SCHEDULE_CHECK[1]:
        started_today = launched >= datetime.combine(local.date(), SCHEDULE_START, IST)
        results["schedule"] = (
            None if running or started_today else f"the server is {instance['State']['Name']}"
        )
    if not running or now - launched < SETTLE:
        return results
    for name, path in (("api", "/api/health"), ("site", "/")):
        try:
            get(f"https://{domain}{path}")
            results[name] = None
        except FAILED as error:
            results[name] = reason(error)
    try:
        days = days_left(domain, now)
        results["certificate"] = None if days >= CERT_MIN_DAYS else f"{days:.0f} days left"
    except FAILED as error:
        results["certificate"] = f"could not be checked: {reason(error)}"
    if now - launched >= BACKUP_SETTLE:
        done = last_backup()
        if done is None:
            results["backup"] = "none found"
        elif now - done >= BACKUP_MAX_AGE:
            results["backup"] = f"the newest is {(now - done) / timedelta(hours=1):.0f} hours old"
        else:
            results["backup"] = None
    return results


def counted(previous: dict[str, int], results: dict[str, str | None]) -> dict[str, int]:
    """Each failing check's failures in a row; a passing or not-run check is dropped."""
    return {name: previous.get(name, 0) + 1 for name, why in results.items() if why is not None}


def message(previous: dict[str, int], now_counts: dict[str, int], results: dict[str, str | None]) -> str:
    """The email for checks that just reached FAILURES_TO_ALERT and those that recovered; empty, none."""
    lines = [
        f"FAILING: {PROBLEMS[name]} ({results[name]})."
        for name, count in now_counts.items()
        if count == FAILURES_TO_ALERT
    ]
    lines += [
        f"OK again: {PROBLEMS[name]}, no longer."
        for name, count in previous.items()
        if count >= FAILURES_TO_ALERT and results.get(name, "not run") is None
    ]
    return "\n".join(lines)


def run(ec2: Any, s3: Any, ssm: Any, sns: Any, env: dict[str, str], now: datetime, **probes: Any) -> str:
    """One round: the checks, the counts and at most one email. Returns the email sent, or ""."""
    reply = ec2.describe_instances(InstanceIds=[env["INSTANCE_ID"]])
    instance = reply["Reservations"][0]["Instances"][0]
    results = checks(
        instance, now, env["SITE_DOMAIN"], lambda: newest_done(s3, env["BACKUP_BUCKET"]), **probes
    )
    try:
        previous = json.loads(ssm.get_parameter(Name=STATE_PARAMETER)["Parameter"]["Value"])
    except ClientError as error:
        if error.response["Error"]["Code"] != "ParameterNotFound":
            raise
        previous = {}
    now_counts = counted(previous, results)
    text = message(previous, now_counts, results)
    if text:  # before the counts: a failed publish raises, so the next round sends it again
        sns.publish(
            TopicArn=env["TOPIC_ARN"],
            Subject="Kaagaz monitor",
            Message=f"{text}\n\nSite: {env['SITE_DOMAIN']}",
        )
    if now_counts != previous:
        ssm.put_parameter(Name=STATE_PARAMETER, Value=json.dumps(now_counts), Type="String", Overwrite=True)
    log.info("monitor: %s", json.dumps(results, sort_keys=True))
    return text


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    env = {name: os.environ[name] for name in ("INSTANCE_ID", "SITE_DOMAIN", "BACKUP_BUCKET", "TOPIC_ARN")}
    sent = run(
        boto3.client("ec2"),
        boto3.client("s3"),
        boto3.client("ssm"),
        boto3.client("sns"),
        env,
        datetime.now(UTC),
    )
    return {"alerted": bool(sent)}
