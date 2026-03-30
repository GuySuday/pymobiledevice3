import re
from datetime import datetime
from io import StringIO

import pytest

from pymobiledevice3.cli import syslog as syslog_module
from pymobiledevice3.services.os_trace import SyslogEntry, SyslogLogLevel

_FAKE_SERVICE_PROVIDER = object()


class _FakeOsTraceService:
    def __init__(self, entries):
        self._entries = entries

    async def syslog(self, pid=-1):
        for entry in self._entries:
            yield entry


def _create_syslog_entry(message: str) -> SyslogEntry:
    return SyslogEntry(
        pid=123,
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        level=SyslogLogLevel.INFO,
        image_name="/usr/libexec/test-process",
        image_offset=0,
        filename="/usr/libexec/test-process",
        message=message,
    )


def _create_syslog_entries(messages: list[str]) -> list[SyslogEntry]:
    return [_create_syslog_entry(message) for message in messages]


async def _run_syslog_live(
    monkeypatch, capsys, entries: list[SyslogEntry], **syslog_live_kwargs
) -> tuple[list[str], str]:
    monkeypatch.setattr(syslog_module, "OsTraceService", lambda lockdown: _FakeOsTraceService(entries))
    monkeypatch.setattr(syslog_module, "user_requested_colored_output", lambda: False)

    out = syslog_live_kwargs.pop("out", None)
    kwargs = {
        "pid": -1,
        "process_name": None,
        "match": [],
        "invert_match": [],
        "match_insensitive": [],
        "invert_match_insensitive": [],
        "include_label": False,
        "regex": [],
        "insensitive_regex": [],
    }
    kwargs.update(syslog_live_kwargs)

    await syslog_module.syslog_live(
        service_provider=_FAKE_SERVICE_PROVIDER,
        out=out,
        **kwargs,
    )

    printed_lines = capsys.readouterr().out.strip().splitlines()
    return printed_lines, "" if out is None else out.getvalue()


@pytest.mark.parametrize(
    ("invert_match", "invert_match_insensitive", "expected"),
    [
        ([], [], False),
        (["match"], [], True),
        (["MobileSafari"], [], True),
        (["dont"], [], False),
        ([], ["MaTCh"], True),
        ([], ["mobilesafari"], True),
        ([], ["missing"], False),
        (["match"], ["missing"], True),
        (["dont"], ["MaTCh"], True),
        (["dont"], ["missing"], False),
        (["match", "dont"], [], True),
        ([], ["missing", "MaTCh"], True),
        (["dont", "missing"], ["absent"], False),
    ],
)
def test_should_skip_line(invert_match: list[str], invert_match_insensitive: list[str], expected: bool) -> None:
    assert (
        syslog_module._should_skip_line(
            "MobileSafari match",
            invert_match,
            invert_match_insensitive,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("match", "match_insensitive", "match_regex", "expected"),
    [
        ([], [], [], True),
        (["match"], [], [], True),
        (["MobileSafari"], [], [], True),
        (["missing"], [], [], False),
        (["MobileSafari", "match"], [], [], True),
        (["MobileSafari", "missing"], [], [], False),
        ([], ["mobilesafari"], [], True),
        ([], ["match"], [], True),
        ([], ["missing"], [], False),
        ([], ["mobilesafari", "match"], [], True),
        ([], ["mobilesafari", "missing"], [], False),
        (["match"], ["mobilesafari"], [], True),
        (["MobileSafari"], ["match"], [], True),
        (["match"], ["missing"], [], False),
        (["missing"], ["mobilesafari"], [], False),
        (["MobileSafari", "match"], ["mobilesafari", "match"], [], True),
        (["MobileSafari", "match"], ["mobilesafari", "missing"], [], False),
        ([], [], [syslog_module.re.compile(r".*(match).*")], True),
        ([], [], [syslog_module.re.compile(r".*(missing).*")], False),
        ([], [], [syslog_module.re.compile(r".*(match).*"), syslog_module.re.compile(r".*(missing).*")], True),
        (["match"], [], [syslog_module.re.compile(r".*(MobileSafari).*")], True),
        (["match"], [], [syslog_module.re.compile(r".*(missing).*")], False),
        ([], ["mobilesafari"], [syslog_module.re.compile(r".*(match).*")], True),
        ([], ["mobilesafari"], [syslog_module.re.compile(r".*(missing).*")], False),
    ],
)
def test_should_keep_line(
    match: list[str], match_insensitive: list[str], match_regex: list[re.Pattern[str]], expected: bool
) -> None:
    assert (
        syslog_module._should_keep_line(
            "MobileSafari match",
            match,
            match_insensitive,
            match_regex,
        )
        == expected
    )


@pytest.mark.asyncio
async def test_syslog_live_invert_match(monkeypatch, capsys):
    printed_lines, _ = await _run_syslog_live(
        monkeypatch,
        capsys,
        _create_syslog_entries(["keep this line", "skip this line"]),
        invert_match=["skip"],
    )
    assert len(printed_lines) == 1
    assert printed_lines[0].endswith("keep this line")


@pytest.mark.asyncio
async def test_syslog_live_match(monkeypatch, capsys):
    printed_lines, _ = await _run_syslog_live(
        monkeypatch,
        capsys,
        _create_syslog_entries(["daemon ready", "daemon error", "worker ready"]),
        match=["daemon"],
        invert_match=["error"],
    )
    assert len(printed_lines) == 1
    assert printed_lines[0].endswith("daemon ready")


@pytest.mark.asyncio
async def test_syslog_live_invert_match_uses_disjunction_for_repeated_values(monkeypatch, capsys):
    printed_lines, _ = await _run_syslog_live(
        monkeypatch,
        capsys,
        _create_syslog_entries(["daemon ready", "daemon ready error", "worker error"]),
        invert_match=["daemon", "error"],
    )
    assert len(printed_lines) == 0


@pytest.mark.asyncio
async def test_syslog_live_match_insensitive(monkeypatch, capsys):
    printed_lines, _ = await _run_syslog_live(
        monkeypatch,
        capsys,
        _create_syslog_entries(["MobileSafari ready", "backboardd ready", "Worker READY"]),
        match_insensitive=["mobilesafari"],
        invert_match_insensitive=["ready"],
    )
    assert len(printed_lines) == 0


@pytest.mark.asyncio
async def test_syslog_live_process_name_and_start_after_filter_output(monkeypatch, capsys):
    entries = [
        _create_syslog_entry("warmup"),
        SyslogEntry(
            pid=123,
            timestamp=datetime(2024, 1, 1, 0, 0, 1),
            level=SyslogLogLevel.INFO,
            image_name="/usr/libexec/other-process",
            image_offset=0,
            filename="/usr/libexec/other-process",
            message="START now",
        ),
        _create_syslog_entry("before START"),
        _create_syslog_entry("START now"),
        _create_syslog_entry("after start"),
    ]

    printed_lines, _ = await _run_syslog_live(
        monkeypatch,
        capsys,
        entries,
        process_name="test-process",
        start_after="START",
    )
    assert len(printed_lines) == 4
    assert printed_lines[0] == 'Waiting for "START" ...'
    assert printed_lines[1].endswith("before START")
    assert printed_lines[2].endswith("START now")
    assert printed_lines[3].endswith("after start")


@pytest.mark.asyncio
async def test_syslog_live_regex_filters_and_writes_plain_output_to_out(monkeypatch, capsys):
    entries = [
        _create_syslog_entry("daemon ready"),
        _create_syslog_entry("worker ready"),
        _create_syslog_entry("springboard ready"),
    ]
    out = StringIO()

    printed_lines, out_value = await _run_syslog_live(
        monkeypatch,
        capsys,
        entries,
        out=out,
        regex=["daemon", "worker"],
    )
    out_lines = out_value.strip().splitlines()
    assert len(printed_lines) == 2
    assert len(out_lines) == 2
    assert printed_lines[0].endswith("daemon ready")
    assert printed_lines[1].endswith("worker ready")
    assert out_lines == printed_lines
