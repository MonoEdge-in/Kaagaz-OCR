"""The monitor's Lambda (infra/aws/monitor/handler.py, #480) with fakes for EC2, S3, SSM and SNS.

The handler lives outside the backend package, so it is loaded from its path, as
tests/test_wake_handler.py loads the Wake link. No network: the HTTPS and certificate probes
are passed in.
"""

import importlib.util
import json
import os
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from botocore.exceptions import ClientError

HANDLER = Path(__file__).resolve().parents[2] / "infra" / "aws" / "monitor" / "handler.py"
_spec = importlib.util.spec_from_file_location("kaagaz_monitor_handler", HANDLER)
assert _spec is not None and _spec.loader is not None
monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(monitor)

ENV = {
    "INSTANCE_ID": "i-0123456789abcdef0",
    "SITE_DOMAIN": "kaagaz.example.com",
    "BACKUP_BUCKET": "kaagaz-backups-example",
    "TOPIC_ARN": "arn:aws:sns:ap-south-1:111122223333:kaagaz-alerts",
}
# Tuesday 3 Nov 2026, 15:00 IST: outside the schedule window.
AFTERNOON = datetime(2026, 11, 3, 9, 30, tzinfo=UTC)


class FakeEc2:
    def __init__(self, state: str, launched: datetime) -> None:
        self.instance = {"State": {"Name": state}, "LaunchTime": launched}

    def describe_instances(self, InstanceIds: list[str]) -> dict[str, Any]:
        assert InstanceIds == [ENV["INSTANCE_ID"]]
        return {"Reservations": [{"Instances": [self.instance]}]}


class FakeS3:
    def __init__(self, pages: list[list[tuple[str, datetime]]]) -> None:
        self.pages = pages

    def get_paginator(self, name: str) -> "FakeS3":
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket: str) -> list[dict[str, Any]]:
        assert Bucket == ENV["BACKUP_BUCKET"]
        return [
            {"Contents": [{"Key": key, "LastModified": when} for key, when in page]} for page in self.pages
        ]


class FakeSsm:
    def __init__(self, value: dict[str, int] | None = None) -> None:
        self.value = None if value is None else json.dumps(value)
        self.writes = 0

    def get_parameter(self, Name: str) -> dict[str, Any]:
        assert Name == "/kaagaz/monitor/failures"
        if self.value is None:
            raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
        return {"Parameter": {"Value": self.value}}

    def put_parameter(self, Name: str, Value: str, Type: str, Overwrite: bool) -> None:
        assert (Name, Type, Overwrite) == ("/kaagaz/monitor/failures", "String", True)
        self.value = Value
        self.writes += 1

    def counts(self) -> dict[str, int]:
        return {} if self.value is None else dict(json.loads(self.value))


class FakeSns:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def publish(self, TopicArn: str, Subject: str, Message: str) -> None:
        assert (TopicArn, Subject) == (ENV["TOPIC_ARN"], "Kaagaz monitor")
        self.sent.append(Message)


def fresh_backup(now: datetime) -> FakeS3:
    return FakeS3(
        [
            [
                ("20261102/kaagaz.dump.cms", now - timedelta(hours=2)),
                ("20261102/done", now - timedelta(hours=2)),
            ]
        ]
    )


def ok(url: str) -> None:
    return None


def days(left: float) -> Any:
    return lambda domain, now: left


def down(url: str) -> None:
    raise urllib.error.HTTPError(url, 503, "Service Unavailable", Message(), None)


class Round:
    """One server, rounds of the monitor against it."""

    def __init__(self, ssm: FakeSsm | None = None) -> None:
        self.ssm = ssm or FakeSsm()
        self.sns = FakeSns()

    def run(self, ec2: FakeEc2, s3: FakeS3, now: datetime, **probes: Any) -> str:
        probes = {"get": ok, "days_left": days(60), **probes}
        return str(monitor.run(ec2, s3, self.ssm, self.sns, ENV, now, **probes))


def up_for(hours: float, now: datetime = AFTERNOON) -> FakeEc2:
    return FakeEc2("running", now - timedelta(hours=hours))


def test_a_healthy_server_sends_nothing_and_writes_nothing() -> None:
    server = Round(FakeSsm({}))
    assert server.run(up_for(3), fresh_backup(AFTERNOON), AFTERNOON) == ""
    assert (server.sns.sent, server.ssm.writes) == ([], 0)


def test_a_stopped_or_just_started_server_is_not_checked() -> None:
    """The server stops itself when idle: no alarm every night."""

    def never(url: str) -> None:
        raise AssertionError("no request while the server is stopped or settling")

    for ec2 in (FakeEc2("stopped", AFTERNOON - timedelta(days=1)), up_for(0.1)):
        server = Round()
        assert server.run(ec2, FakeS3([]), AFTERNOON, get=never) == ""
        assert (server.sns.sent, server.ssm.counts()) == ([], {})


def test_one_failure_waits_the_second_alerts_once_and_a_pass_says_ok_again() -> None:
    server = Round()
    backup = fresh_backup(AFTERNOON)
    assert server.run(up_for(3), backup, AFTERNOON, get=down) == ""  # 1: no email yet
    assert server.ssm.counts() == {"api": 1, "site": 1}
    sent = server.run(up_for(3), backup, AFTERNOON, get=down)  # 2: one email
    assert sent == (
        "FAILING: The API health page does not answer (HTTP 503).\n"
        "FAILING: The website does not answer (HTTP 503)."
    )
    assert server.sns.sent == [sent + "\n\nSite: kaagaz.example.com"]
    assert server.run(up_for(3), backup, AFTERNOON, get=down) == ""  # 3: still failing, no repeat
    assert server.ssm.counts() == {"api": 3, "site": 3}
    assert server.run(up_for(3), backup, AFTERNOON) == (
        "OK again: The API health page does not answer, no longer.\n"
        "OK again: The website does not answer, no longer."
    )
    assert server.ssm.counts() == {} and len(server.sns.sent) == 2


def test_failures_in_a_row_only() -> None:
    server = Round()
    backup = fresh_backup(AFTERNOON)
    server.run(up_for(3), backup, AFTERNOON, get=down)
    server.run(up_for(3), backup, AFTERNOON)  # passed in between
    assert server.run(up_for(3), backup, AFTERNOON, get=down) == ""
    assert server.ssm.counts() == {"api": 1, "site": 1}


def test_a_check_that_stops_running_is_forgotten() -> None:
    """Stopped mid-outage: the next start counts from nothing (and alerts again if still down)."""
    server = Round(FakeSsm({"api": 2}))
    assert server.run(FakeEc2("stopped", AFTERNOON - timedelta(hours=5)), FakeS3([]), AFTERNOON) == ""
    assert server.ssm.counts() == {}


def test_the_certificate_needs_14_days() -> None:
    server = Round(FakeSsm({"certificate": 1}))
    assert server.run(up_for(3), fresh_backup(AFTERNOON), AFTERNOON, days_left=days(10.2)) == (
        "FAILING: The HTTPS certificate expires soon (10 days left)."
    )

    def refused(domain: str, now: datetime) -> float:
        raise ConnectionRefusedError

    assert monitor.checks(up_for(3).instance, AFTERNOON, "d", lambda: AFTERNOON, ok, refused)[
        "certificate"
    ] == ("could not be checked: ConnectionRefusedError")
    assert (
        monitor.checks(up_for(3).instance, AFTERNOON, "d", lambda: AFTERNOON, ok, days(14))["certificate"]
        is None
    )


def test_the_backup_must_be_under_26_hours_old_once_the_server_has_run_an_hour() -> None:
    def backup_result(s3: FakeS3, hours_up: float = 3) -> Any:
        def last() -> Any:
            return monitor.newest_done(s3, ENV["BACKUP_BUCKET"])

        return monitor.checks(up_for(hours_up).instance, AFTERNOON, "d", last, ok, days(60)).get(
            "backup", "not run"
        )

    old = AFTERNOON - timedelta(hours=27)
    assert backup_result(fresh_backup(AFTERNOON)) is None
    assert backup_result(FakeS3([[("20261101/done", old)]])) == "the newest is 27 hours old"
    # Only done counts: a night that never finished is no backup.
    assert backup_result(FakeS3([[("20261103/files.tar.cms", AFTERNOON)]])) == "none found"
    assert backup_result(FakeS3([])) == "none found"
    assert backup_result(FakeS3([]), hours_up=0.5) == "not run"  # the boot-time backup may still run


def test_newest_done_reads_every_page() -> None:
    late = AFTERNOON - timedelta(hours=1)
    s3 = FakeS3(
        [
            [("20261101/done", AFTERNOON - timedelta(days=2))],
            [("20261103/done", late), ("x/done.txt", AFTERNOON)],
        ]
    )
    assert monitor.newest_done(s3, ENV["BACKUP_BUCKET"]) == late


@pytest.mark.parametrize(
    ("ist", "state", "launched_ist", "expected"),
    [
        # Monday 2 Nov 2026, 09:50 IST.
        ((2, 9, 50), "stopped", (1, 18, 0), "the server is stopped"),  # last started yesterday
        ((2, 9, 50), "stopped", (2, 9, 31), None),  # the schedule started it; it stopped itself since
        ((2, 9, 50), "running", (1, 18, 0), None),  # running since yesterday
        ((2, 9, 40), "stopped", (1, 18, 0), "not run"),  # before 09:45
        ((2, 10, 0), "stopped", (1, 18, 0), "not run"),  # after 10:00
        ((7, 9, 50), "stopped", (6, 18, 0), "not run"),  # Saturday
    ],
)
def test_the_weekday_schedule_check(
    ist: tuple[int, int, int], state: str, launched_ist: tuple[int, int, int], expected: str | None
) -> None:
    def at(day: int, hour: int, minute: int) -> datetime:
        return datetime(2026, 11, day, hour, minute, tzinfo=monitor.IST).astimezone(UTC)

    instance = FakeEc2(state, at(*launched_ist)).instance
    assert (
        monitor.checks(instance, at(*ist), "d", lambda: None, ok, days(60)).get("schedule", "not run")
        == expected
    )


def test_the_schedule_alert_comes_on_the_second_round_in_the_window() -> None:
    server = Round()
    stopped = FakeEc2("stopped", datetime(2026, 11, 1, 12, 0, tzinfo=UTC))
    first = datetime(2026, 11, 2, 4, 15, tzinfo=UTC)  # 09:45 IST
    assert server.run(stopped, FakeS3([]), first) == ""
    assert server.run(stopped, FakeS3([]), first + timedelta(minutes=5)) == (
        "FAILING: The weekday 09:30 schedule did not start the server (the server is stopped)."
    )


def test_reasons_are_short_codes_never_bodies() -> None:
    assert monitor.reason(urllib.error.HTTPError("u", 502, "Bad Gateway", Message(), None)) == "HTTP 502"
    assert monitor.reason(urllib.error.URLError(TimeoutError("timed out"))) == "TimeoutError"
    assert monitor.reason(ConnectionResetError()) == "ConnectionResetError"


def test_a_state_read_error_other_than_not_found_stops_the_round() -> None:
    class BrokenSsm(FakeSsm):
        def get_parameter(self, Name: str) -> dict[str, Any]:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetParameter")

    with pytest.raises(ClientError):
        monitor.run(
            up_for(3),
            fresh_backup(AFTERNOON),
            BrokenSsm(),
            FakeSns(),
            ENV,
            AFTERNOON,
            get=ok,
            days_left=days(60),
        )


def test_the_handler_reads_its_settings_and_makes_its_clients() -> None:
    made: list[str] = []
    clients = {
        "ec2": up_for(3, datetime.now(UTC)),
        "s3": fresh_backup(datetime.now(UTC)),
        "ssm": FakeSsm({}),
        "sns": FakeSns(),
    }

    def client(name: str) -> Any:
        made.append(name)
        return clients[name]

    with (
        mock.patch.dict(os.environ, ENV),
        mock.patch.object(monitor.boto3, "client", client),
        mock.patch.object(monitor, "fetch", ok),
        mock.patch.object(monitor, "cert_days_left", days(60)),
    ):
        assert monitor.handler({}, None) == {"alerted": False}
    assert made == ["ec2", "s3", "ssm", "sns"]
