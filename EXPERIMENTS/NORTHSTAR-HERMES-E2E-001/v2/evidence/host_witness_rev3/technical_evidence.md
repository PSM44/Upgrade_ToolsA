# Technical evidence — NORTHSTAR-P2-HOST-WITNESS-REV3

## Observations, 2026-11-12
- Remote PR #9 and local P2 baseline: e096b6935f7e0be34186e3de21def4b77ca6f845; PR OPEN/Draft.
- Original source SHA256 9ede222e391dd8239343d305988cccdb811517567e44b9b9543aa36989aef8a7; historical QA-augmented source b80bcc4cfce15d97992ea0db851d66c3ca7996fbd45d46d7dae388ae3206d81b. Neither modified.
- Full rejection recovered verbatim from its original reviewer: fe1b862ac242d5e2ae712b512e7dd5fadd214ffa7853e6432329323c77367745. It is not the shorter PR closure summary.
- PowerShell $PSVersionTable.PSVersion: 7.6.9; [Environment]::OSVersion.VersionString: Microsoft Windows NT 10.0.26300.0.
- Get-Command schtasks.exe: C:\Windows\system32\schtasks.exe. /Create /? lists XML and valid schedule types; ONDEMAND is not among them.
- whoami /groups: medium integrity; Administrators deny-only; Performance Log Users absent. Omitted personal/group identifiers unrelated to the claim.
- Debian id: uid=1000(psm), groups include sudo; stat: state directory psm/psm 0700, checkout psm/psm 0755.
- /proc/sys/fs/binfmt_misc/WSLInterop entry PRESENT. No claim about usable Windows elevation; no such attempt made.
- openssl version: OpenSSL 3.5.6; python3 --version: 3.13.5.
- get-command pwsh resolves to Codex bundled runtime in this tool session, not an independently provisioned witness binary. Do not accredit that path.
- G1/G2 rights and effective isolation remain unverified. No ETW/session, ACL, account, task or global configuration changed.

## Scheduler syntax probe (read-only validation)
PowerShell COM Schedule.Service.Connect → NewTask(0).
Set AllowDemandStart=true, MultipleInstances=2 (IgnoreNew), ExecutionTimeLimit=PT30M, current interactive principal (LogonType=3, RunLevel=0), zero triggers, action pwsh with inert "-NoProfile -NonInteractive -Command exit".
TaskFolder.RegisterTask(name, XmlText, 1, null, null, 3, null) used TASK_VALIDATE_ONLY exclusively.
Observed output:
```json
{"ValidationOnly":true,"Triggers":0,"AllowDemandStart":true,"MultipleInstances":2,"ValidationReturnedObject":false}
```
GetTask for NorthStar-Rev3-ValidateOnly-NOTREGISTERED returned not-found after validation.
This validates syntax on the local scheduler; no action ran, no task registered. Principal/key/ETW integration and real demand start were NOT tested.

## Source references
Design proposals and threat analysis are our engineering requirements, not assertions that Microsoft supplies an end-to-end trusted witness.
- [StartTrace permissions and logger lifecycle](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/nf-evntrace-starttracea)
- [Event trace loss counters](https://learn.microsoft.com/en-us/windows/win32/api/evntrace/ns-evntrace-event_trace_properties)
- [Kernel process events and payload identities](https://learn.microsoft.com/en-us/windows/win32/etw/process)
- [Event consumption and buffer callbacks](https://learn.microsoft.com/en-us/windows/win32/etw/consuming-events)
- [TASK_VALIDATE_ONLY](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskfolder-registertask)
- [AllowStartOnDemand](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-allowstartondemand-settingstype-element)
- [Schtasks create/XML](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create)
- [.NET explicit signature format](https://learn.microsoft.com/en-us/dotnet/api/system.security.cryptography.ecdsa.signdata?view=net-10.0)
- [WSL permissions/UNC boundary](https://learn.microsoft.com/en-us/windows/wsl/file-permissions)

## Verification limits
No observer implementation, no P1/UWS suite rerun, no provider calls, no systemwide NTV_ROOT claim. Cryptographic primitive probes, if included below, use ephemeral TEST keys only and do not validate the witness protocol. Reviewer is a separate agent with rejection authority but shares OS/tools, not an independent security principal.

## Offline cryptographic probe (observed)
Ephemeral .NET P-256 TEST key, no private key persisted. Python json.dumps canonical bytes equal the PowerShell-written ASCII body, including 9007199254740993 and escaped non-ASCII text.
Python subprocess captured actual OpenSSL return codes: DER valid=0; altered signature-domain body=1; P1363 passed as DER=1. See crypto_probe_results.json and the public-only crypto_probe/ vector; run_crypto_probe.py reproduces verification with Python stdlib/OpenSSL.
Initial diagnostic attempt used the wrong PowerShell nested-type spelling and failed before generating a key; corrected to ECCurve+NamedCurves. An intervening shell echo misreported return codes; those labels are discarded in favor of Python subprocess.returncode. No witness/security configuration was touched.
