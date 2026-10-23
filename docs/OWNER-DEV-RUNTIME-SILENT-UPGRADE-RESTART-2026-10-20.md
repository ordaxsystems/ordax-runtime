# Silent Windows Runtime start after upgrades

## Incident

A checksum-verified Owner/Dev installer 0.15.9 upgraded the private Runtime from 0.4.6 to 0.4.7 through a silent install. The HKCU startup entry remained present, but the Runtime process was stopped after the installer exited, leaving the local status listener unavailable and MCP requests pending.

Starting the packaged Runtime manually restored the listener; seven subsequent device terminal/process actions completed. This confirmed that the failure was local process recovery, rather than OAuth or a remote grant.

## Root cause and correction

The Inno Setup Run section launched only the optional Studio, and that launch used the skip-if-silent flag. The installer correctly quiesced the existing Runtime before replacing files, but the silent path did not restart it.

PR #164 added the packaged Runtime as a hidden, asynchronous post-install launch for both silent and interactive installs. Studio remains optional. The Windows smoke now checks that exactly one packaged Runtime reaches readiness after install, upgrade, and repair, before Studio can mask a missing restart.

## Validation and limits

Windows Product Build run #612 passed on commit 5830e69 and executed the hosted Windows install/upgrade smoke. The smoke validates the packaged process and local Runtime readiness.

The reported manual recovery predates the fix. This record does not claim that the corrected installer was installed on the same physical computer, or that a signed public release has been published. CI proves the hosted installer flow only.

## Version metadata

The Runtime package and compatibility contract move from 0.4.7 to 0.4.8 so the next Runtime build identifies the installer recovery fix. This version change does not itself create or publish a release artifact.
