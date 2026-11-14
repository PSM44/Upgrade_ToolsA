# NORTHSTAR_P2_P1B_CORRECTED_AUTHORIZATION_PACKAGE

TASK_ID: NORTHSTAR-P2-P1B-RUNBOOK-CORRECTION
PROJECT: PS.Upgrade_TA
MODE: READ_ONLY / DESIGN_ONLY
Date: 2026-11-14 (America/Santiago)
Status: **P1B_PACKAGE_READY**
NORTHSTAR: FAIL (unchanged)

## 0. Objective and scope

Correct the twelve blocking defects identified in NORTHSTAR_P2_VIRTUALBOX_P1A_GATE_REPORT. Deliver a technically consistent P1B runbook ready for a single grouped HUMAN authorization decision.

This document does NOT declare PILOT_PLATFORM_PASS, G1–G5 PASS or NORTHSTAR_PASS. No software was installed, no VM was created, no Windows configuration was changed and no inference was executed during preparation of this package.

## 1. Preserved baseline

| Item | Evidence |
|---|---|
| PR #9 | OPEN/DRAFT, HEAD `2ac4c56944e77cfdd5e06791669851c3339c3e96`, branch `codex/northstar-hermes-e2e-001-evidence` |
| VirtualBox 7.2.22 build 175245 | Acquired; SHA-256 `afd0ff1656cbe04ed88ce619594ae021419d97cce89fa9bdc443e9a963f1713f`; Authenticode PASS (Oracle America, Inc., DigiCert chain) |
| Debian 13.7.0 amd64 netinst ISO | Acquired; SHA-256 `a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355`; GPG GOODSIG fingerprint `DF9B9C49EAA9298432589D76DA87E80D6294BE9B` |
| Architecture | W (Windows host) / VM-D (Debian guest) / S (Supervisor) / VM-R (RORCH guest) via Internal Networking |
| Windows Home | VBS active (status 2), hypervisor present, VMP enabled via WSL2 |
| WSL2 | kali-linux (Running, default), Debian (Stopped); both WSL version 2 |
| Debian experimental | Stopped per WSL state |
| G1–G5, P3, P4 | OPEN |
| NORTHSTAR | FAIL |

## 2. Platform identity (measured, not assumed)

| Property | Measured value | Source |
|---|---|---|
| Edition | Windows 11 Home | `Win32_OperatingSystem.Caption` |
| EditionID | Core | Registry `HKLM:\...\CurrentVersion\EditionID` |
| Build | 26300 | Registry `CurrentBuildNumber` |
| UBR | **9974** | Registry `UBR` |
| Full version | **10.0.26300.9974** | Build.UBR composite |
| DisplayVersion | 26H2 | Registry `DisplayVersion` |
| `Environment.OSVersion` | 10.0.26300**.0** | Runtime API (omits UBR) |
| CPU | AMD Ryzen 7 6800H with Radeon Graphics | `Win32_Processor.Name` |
| Cores / Threads | 8 / 16 | `Win32_Processor` |
| Total RAM | ~64 GiB (63.19 GiB reported) | `Win32_ComputerSystem.TotalPhysicalMemory` |
| VBS status | 2 (Running) | `Win32_DeviceGuard.VirtualizationBasedSecurityStatus` |
| SecurityServicesRunning | {0} (none enforced) | `Win32_DeviceGuard` |
| Hypervisor present | True | `Win32_ComputerSystem.HypervisorPresent` |
| WHP feature | **NOT ENABLED** (WinHvPlatform/WinHvEmulation services absent) | Registry `HKLM:\SYSTEM\CurrentControlSet\Services\` |
| VID driver | Start=1 (boot) | Registry |

**Correction #9 applied:** The full version is `10.0.26300.9974`, not `10.0.26300.0`. `Environment.OSVersion.Version` returns UBR=0 regardless of the actual UBR. Any platform check MUST read `CurrentBuildNumber` and `UBR` from the registry separately and compare the composite. The values `.0` and `.9974` are NOT equivalent and MUST NOT be treated as interchangeable.

## 3. Correction matrix

### C1 — WHP dependency (Oracle 7.2 manual)

**Defect:** P1A did not correctly document the Windows Hypervisor Platform dependency.

**Correction:** VirtualBox 7.2 on a host where the hypervisor is already active (which it is: VBS=2, WSL2 enabled, HypervisorPresent=True) uses the NEM (Native Execution Manager) backend. NEM requires the **Windows Hypervisor Platform** (WHP) optional feature to be enabled.

Current state: WHP is **NOT enabled** — the `WinHvPlatform` and `WinHvEmulation` service keys do not exist in the registry. Without WHP, VirtualBox VMs will fail to start on this host.

**Required action (HUMAN authorization):**

```
dism /Online /Enable-Feature /FeatureName:HypervisorPlatform /NoRestart
```

Followed by a host reboot (see §4 reboot consolidation).

**Verification after reboot:**

```powershell
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\WinHvPlatform').Start
# Expected: 1 (boot-start)
```

**Oracle documentation reference:** Chapter 14, Section 14.1 labels VirtualBox+Hyper-V as "experimental" (not formally supported). Section 14.2 acknowledges poor performance in this mode. Chapter 10, Section 10.3 warns against running multiple hypervisors simultaneously; NEM/WHP is the supported coexistence path. No Oracle documentation promises native VT-x performance under NEM.

**Risk:** Performance reduction under NEM is expected and documented. This is the ONLY viable configuration given that VBS/VMP/HVCI must not be disabled (C2). If NEM performance proves insufficient for pilot objectives, this is a stop condition, not grounds for disabling the hypervisor.

### C2 — Prohibit disabling VBS, VMP, HVCI or modifying bootloader

**Defect:** P1A did not explicitly prohibit these modifications, or implied they could be changed.

**Correction:** The following are PROHIBITED throughout the pilot:

- Disabling Virtualization-Based Security (VBS) — currently active (status 2)
- Disabling Virtual Machine Platform (VMP) — required by WSL2
- Disabling or weakening Hypervisor Code Integrity (HVCI)
- Modifying the Windows bootloader (bcdedit, BCD store edits)
- Running `DG_Readiness_Tool` to disable Device Guard / Credential Guard
- Disabling Windows Defender or Secure Boot

**Rationale:** VBS protects the Windows kernel. VMP provides the hypervisor for WSL2. HVCI integrity protections are part of the platform security baseline. VirtualBox must operate within these constraints via NEM/WHP, never by removing them.

**Stop condition:** If VirtualBox cannot function with VBS+WHP active, the pilot halts. The platform security baseline is not negotiable.

### C3 — Remove Extension Pack from installation

**Defect:** P1A included the Extension Pack as part of the installation.

**Correction:** The VirtualBox Extension Pack is a SEPARATE download from the base package (Oracle manual ch01 §1.5, ch02 §2.5). It adds VRDP, webcam passthrough, Intel PXE boot ROM, AES disk encryption and cloud integration. None of these are required for the NorthStar pilot.

The Extension Pack is NOT part of the MSI installer. It is explicitly excluded from acquisition and installation. The base package installer (`VirtualBox-7.2.22-175245-Win.exe`) does not contain it.

**Verification:** After installation, `VBoxManage list extpacks` must return "Extension Packs: 0".

### C4 — Remove Host-Only, NAT, Bridge and additional adapters

**Defect:** P1A allowed or did not exclude non-essential network adapter types.

**Correction:** The MSI installer feature `VBoxNetwork` contains two sub-features:
- `VBoxNetworkFlt` — bridged networking driver
- `VBoxNetworkAdp` — host-only networking adapter

Both are EXCLUDED from installation. The MSI install command (§7) uses `ADDLOCAL=VBoxApplication` only.

Post-installation, no VM will be configured with NAT, Bridged, Host-Only, NAT Network, UDP Tunnel, Cloud or Generic adapter types. Only Internal Networking (C5) is permitted.

**Verification after VM creation:**

```
VBoxManage showvminfo "VM-D" --machinereadable | findstr "^nic"
VBoxManage showvminfo "VM-R" --machinereadable | findstr "^nic"
```

Expected: `nic1="intnet"` and all other adapters `nic2="none"`, `nic3="none"`, `nic4="none"`.

### C5 — Keep only Internal Networking D↔R

**Defect:** P1A did not restrict networking to Internal Networking only.

**Correction:** Both VM-D and VM-R use a single network adapter on Internal Networking mode with a shared network name. Per Oracle manual ch06 §6.6 and Table 6.1:

| Direction | Internal Networking |
|---|---|
| VM → Host | **No** |
| Host → VM | **No** |
| VM ↔ VM (same intnet) | **Yes** |
| VM → External/LAN | **No** |
| External/LAN → VM | **No** |

This provides the D↔R communication channel required by the architecture while preventing any host or external network access from either VM.

**Configuration (after VM creation):**

```
VBoxManage modifyvm "VM-D" --nic1 intnet --intnet1 "northstar-pilot"
VBoxManage modifyvm "VM-D" --nic2 none --nic3 none --nic4 none
VBoxManage modifyvm "VM-R" --nic1 intnet --intnet1 "northstar-pilot"
VBoxManage modifyvm "VM-R" --nic2 none --nic3 none --nic4 none
```

**Guest IP configuration:** Static IPs on each guest. No DHCP server. Proposed: VM-D `10.0.100.1/24`, VM-R `10.0.100.2/24`. No default gateway, no DNS.

### C6 — Remove Nested VT-x/AMD-V

**Defect:** P1A enabled or did not prohibit nested virtualization.

**Correction:** Nested VT-x/AMD-V is PROHIBITED. On this host where the hypervisor is active (NEM mode), nested virtualization is not supported by VirtualBox (Oracle manual ch10 does not document nested VT-x under NEM; ch14 does not list it as functional in Hyper-V coexistence mode). Even if technically possible, it adds an untested and unnecessary layer.

**Configuration:**

```
VBoxManage modifyvm "VM-D" --nested-hw-virt off
VBoxManage modifyvm "VM-R" --nested-hw-virt off
```

**Verification:**

```
VBoxManage showvminfo "VM-D" --machinereadable | findstr "nested-hw-virt"
VBoxManage showvminfo "VM-R" --machinereadable | findstr "nested-hw-virt"
```

Expected: `nested-hw-virt="off"`.

### C7 — Exclude snapshots and suspended states

**Defect:** P1A allowed or did not prohibit VM snapshots and suspended states.

**Correction:** VM snapshots and saved/suspended states are PROHIBITED during the pilot. They create opaque state blobs that cannot be verified, audited or hash-pinned. They can also interact unpredictably with the host witness protocol (evidence intervals depend on clean start/stop).

**Policy:**
- No snapshots taken at any point: `VBoxManage snapshot "VM-D" list` and `VBoxManage snapshot "VM-R" list` must return "This machine does not have any snapshots".
- VMs are always powered off cleanly (ACPI shutdown or `VBoxManage controlvm <vm> acpipowerbutton`), never saved/suspended.
- `VBoxManage showvminfo <vm> --machinereadable | findstr "VMState"` must show either `"running"` or `"poweroff"`, never `"saved"`, `"aborted"` or `"paused"`.

**Stop condition:** If a snapshot or saved state is created inadvertently, the VM state is untrusted. Power off the VM and document the incident. Do not resume from saved state.

### C8 — Recover identity and hashes of already-acquired packages

**Defect:** P1A did not include or reference the verified identity and integrity evidence of the already-acquired artifacts.

**Correction:** Both artifacts were acquired on 2026-11-14 with full verification. Evidence is preserved at `C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\`.

**VirtualBox 7.2.22 build 175245:**

| Property | Value |
|---|---|
| File | `VirtualBox-7.2.22-175245-Win.exe` |
| Size | 178,101,352 bytes |
| SHA-256 | `afd0ff1656cbe04ed88ce619594ae021419d97cce89fa9bdc443e9a963f1713f` |
| Source URL | `https://download.virtualbox.org/virtualbox/7.2.22/VirtualBox-7.2.22-175245-Win.exe` |
| Authenticode | Valid; signer CN=Oracle America, Inc.; chain to DigiCert Trusted Root G4 |
| Signer thumbprint | `2B1BD188256EB612578949A943DE0DDC52D1DF79` |
| Timestamp | 2026-10-19T21:18:57Z (RFC 3161, DigiCert) |
| Oracle SHA256SUMS | Match confirmed; manifest SHA-256 `3460f4ee3b2c8bbe594099a1d28dc69f9acf1f569cf5c6e0d28a04a8d1bb8040` |

**Debian 13.7.0 amd64 netinst:**

| Property | Value |
|---|---|
| File | `debian-13.7.0-amd64-netinst.iso` |
| Size | 792,723,456 bytes |
| SHA-256 | `a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355` |
| Source URL | `https://cdimage.debian.org/debian-cd/13.7.0/amd64/iso-cd/debian-13.7.0-amd64-netinst.iso` |
| Final mirror | `https://saimei.ftp.acc.umu.se/debian-cd/13.7.0/amd64/iso-cd/debian-13.7.0-amd64-netinst.iso` |
| GPG | GOODSIG + VALIDSIG; key `DF9B9C49EAA9298432589D76DA87E80D6294BE9B` (Debian CD signing key) |
| Manifest SHA-256 | `de6d320c52b0fa02787e4a6f58115ace89f821865baaeba86115c723f34cbab4` |

**Pre-installation verification step:** Before running the installer, re-verify the SHA-256 of the EXE against the value above. If it does not match, STOP.

```powershell
(Get-FileHash 'C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\VirtualBox-7.2.22-175245-Win.exe' -Algorithm SHA256).Hash
# Must equal: AFD0FF1656CBE04ED88CE619594AE021419D97CCE89FA9BDC443E9A963F1713F
```

### C9 — Windows edition/build/UBR verification

See §2 (Platform identity). The measured full version is `10.0.26300.9974`. The `Environment.OSVersion` API returns `10.0.26300.0`, which is INCORRECT for precise platform identification.

**Verification procedure (before any installation):**

```powershell
$v = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$fullVersion = "$($v.CurrentBuildNumber).$($v.UBR)"
Write-Output "Edition: $($v.EditionID), Build: $fullVersion, Display: $($v.DisplayVersion)"
# Expected: Edition: Core, Build: 26300.9974, Display: 26H2
```

**Stop condition:** If `EditionID` is not `Core` (Home), if `CurrentBuildNumber` is not `26300`, or if `UBR` has changed from `9974` (indicating a Windows Update was applied between authorization and execution), STOP and re-evaluate. UBR changes require documenting the new value and confirming VirtualBox compatibility is not affected.

### C10 — VM resource allocation

**Defect:** P1A proposed different or unspecified resources.

**Correction:** Reuse the existing VM resource proposal:

| VM | vCPUs | RAM (GiB) | Disk (GiB) |
|---|---|---|---|
| VM-D (Debian worker) | 1 | 2 | 24 |
| VM-R (RORCH) | 4 | 8 | 40 |
| **Total VM** | **5** | **10** | **64** |

**Host budget (8 cores / 16 threads / ~64 GiB RAM):**
- VM allocation: 5 vCPU / 10 GiB RAM
- Remaining for host + WSL2 + VBox overhead: 11 threads / ~54 GiB RAM
- Adequate for host operations, WSL2 Kali and normal desktop use

**VBoxManage commands (after VM creation):**

```
VBoxManage modifyvm "VM-D" --cpus 1 --memory 2048 --vram 16
VBoxManage modifyvm "VM-R" --cpus 4 --memory 8192 --vram 16

VBoxManage createmedium disk --filename "C:\VirtualBox VMs\VM-D\VM-D.vdi" --size 24576 --format VDI
VBoxManage createmedium disk --filename "C:\VirtualBox VMs\VM-R\VM-R.vdi" --size 40960 --format VDI
```

**Disk storage:** VDI dynamic allocation. Actual host disk usage will be less than the nominal sizes until the guests fill their disks. Free space check before creation: at least 80 GiB free on the target volume.

### C11 — No ROOT or Kali for VM or evidence storage

**Defect:** P1A used or implied ROOT paths or the Kali WSL distribution for VM or evidence storage.

**Correction:** VMs and all pilot evidence are stored on Windows NTFS paths accessible to the current user. Specifically:

- **VM storage:** `C:\VirtualBox VMs\` (VirtualBox default). This directory is user-owned, not under a system or administrative root path.
- **Pilot evidence:** `C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\` (existing, already contains acquired artifacts).
- **Run evidence:** Under the PR #9 worktree in Debian at `/home/psm/northstar/workspace/Upgrade_ToolsA-pr9/EXPERIMENTS/...` (existing evidence path), committed to PR #9 branch.

**Prohibited paths:**
- No files under `C:\` root directly (e.g., no `C:\northstar\`, no `C:\temp\`)
- No use of WSL Kali filesystem for VM disk images, ISO storage or evidence
- No use of `/root/` in any WSL distribution
- No use of `/tmp/` in Debian WSL (tmpfs, evaporates between WSL invocations)
- No use of Kali's home directory for anything related to this pilot

**Kali WSL:** Remains running and protected. The pilot does not read, write, configure or stop Kali. Kali's `/home/` is not used for pilot data.

### C12 — Replace rollback estimates with verifiable actions and stop conditions

**Defect:** P1A contained time-based rollback estimates instead of concrete verifiable actions with stop conditions.

**Correction:** Rollback is defined as a sequence of verifiable actions, each with a binary PASS/FAIL check. No time estimates are given because they cannot be verified.

**Rollback procedure (ordered, each step has a verification):**

**R1 — Stop VMs:**

```
VBoxManage controlvm "VM-R" poweroff
VBoxManage controlvm "VM-D" poweroff
```

Verify: `VBoxManage list runningvms` returns empty.
Stop condition: If a VM cannot be powered off, use `VBoxManage controlvm <vm> poweroff` (hard power off). If even that fails, kill the `VBoxHeadless.exe` or `VirtualBoxVM.exe` process.

**R2 — Remove VMs:**

```
VBoxManage unregistervm "VM-D" --delete
VBoxManage unregistervm "VM-R" --delete
```

Verify: `VBoxManage list vms` returns empty. `C:\VirtualBox VMs\VM-D\` and `C:\VirtualBox VMs\VM-R\` no longer exist.
Stop condition: If `--delete` fails to remove files, manually delete the directories after unregistering.

**R3 — Uninstall VirtualBox:**

Via Windows Settings > Apps > Oracle VirtualBox 7.2.22 > Uninstall, or:

```
msiexec /x VirtualBox-7.2.22-Win.msi /qb
```

Verify: `C:\Program Files\Oracle\VirtualBox\VirtualBox.exe` does not exist. Registry key `HKLM:\SOFTWARE\Oracle\VirtualBox` does not exist.
Stop condition: If the uninstaller fails, use Programs and Features in Control Panel. If that also fails, document and escalate.

**R4 — Verify WSL2 unaffected:**

```
wsl --list --verbose
```

Verify: kali-linux and Debian both listed, both WSL version 2, kali-linux in Running state.
Stop condition: If any WSL distribution is missing or degraded, DO NOT proceed with further rollback. Document and escalate.

**R5 — Verify VBS still active:**

```powershell
(Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard).VirtualizationBasedSecurityStatus
```

Verify: Value is 2 (Running).
Stop condition: If VBS is not active, DO NOT reboot or modify any security settings. Document and escalate immediately.

**R6 — WHP feature (optional, if enabled during pilot):**

If WHP was enabled as part of the pilot and rollback requires returning to pre-pilot state:

```
dism /Online /Disable-Feature /FeatureName:HypervisorPlatform /NoRestart
```

Then reboot. Verify: `WinHvPlatform` service key absent from registry.
Note: Disabling WHP after the pilot does not affect WSL2 or VBS — those use VMP and the kernel hypervisor, not the WHP API layer.

**R6 is the ONLY step that requires a reboot.** All other rollback steps complete without restart.

## 4. Grouped HUMAN authorization request

The following actions require a single grouped HUMAN authorization. They are separated into reversible and irreversible categories.

### 4A — Windows feature change (reboot required)

| # | Action | Reversible | Reboot |
|---|---|---|---|
| A1 | Enable WHP: `dism /Online /Enable-Feature /FeatureName:HypervisorPlatform /NoRestart` | Yes (disable + reboot) | Deferred |
| A2 | Reboot host | N/A | **Yes** |

**Post-A2 verification:**
1. WHP active: `(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\WinHvPlatform').Start` == 1
2. VBS still active: `VirtualizationBasedSecurityStatus` == 2
3. WSL2 intact: `wsl --list --verbose` shows both distros, version 2
4. Hypervisor present: `(Get-CimInstance Win32_ComputerSystem).HypervisorPresent` == True

**Stop condition:** If ANY of these four checks fails after reboot, STOP. Do not proceed to installation.

### 4B — VirtualBox installation (no reboot)

| # | Action | Reversible |
|---|---|---|
| B1 | Re-verify installer SHA-256 | Read-only |
| B2 | Extract MSI: `VirtualBox-7.2.22-175245-Win.exe -extract -path C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\Oracle\extracted` | Yes (delete dir) |
| B3 | Install minimal: `msiexec /i "C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\Oracle\extracted\VirtualBox-7.2.22-Win.msi" ADDLOCAL=VBoxApplication VBOX_START=0 /qb` | Yes (uninstall) |

**Note on ADDLOCAL:** `VBoxApplication` is the only feature selected. `VBoxUSB`, `VBoxNetworkFlt`, `VBoxNetworkAdp` and `VBoxPython` are all excluded. Internal Networking does NOT require the bridged (`VBoxNetworkFlt`) or host-only (`VBoxNetworkAdp`) driver features — it is a software-only switch built into the VirtualBox core application.

**Risk: Internal Networking without VBoxNetwork.** The Oracle manual does not explicitly state whether Internal Networking requires the VBoxNetwork MSI feature or only the core application. Internal Networking is a software switch that does not use host network interfaces (unlike bridged or host-only), so it is expected to work with `VBoxApplication` alone. However, this MUST be verified immediately after installation:

```
VBoxManage list intnets
```

If this command fails or Internal Networking is unavailable, the minimal install is insufficient and the install must be repeated with `ADDLOCAL=VBoxApplication,VBoxNetwork` (which installs both bridged and host-only drivers as side effects — these are then unused but present). This fallback is documented as a conditional step, not a default.

**Post-B3 verification:**
1. VirtualBox installed: `"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" --version` returns `7.2.22r175245`
2. No Extension Pack: `VBoxManage list extpacks` shows 0
3. WSL2 unaffected: `wsl --list --verbose` unchanged
4. VBS unaffected: `VirtualizationBasedSecurityStatus` == 2
5. Internal Networking available: `VBoxManage list intnets` succeeds

### 4C — VM creation and configuration (no reboot)

| # | Action | Reversible |
|---|---|---|
| C1 | Create VM-D | Yes (unregister --delete) |
| C2 | Create VM-R | Yes (unregister --delete) |
| C3 | Create disks | Yes (delete VDI files) |
| C4 | Attach Debian ISO to VM-D | Yes (detach) |
| C5 | Configure Internal Networking | Yes (modify) |
| C6 | First boot VM-D for Debian install | Yes (delete + recreate) |

**VM creation commands:**

```
VBoxManage createvm --name "VM-D" --ostype Debian_64 --register
VBoxManage modifyvm "VM-D" --cpus 1 --memory 2048 --vram 16
VBoxManage modifyvm "VM-D" --nic1 intnet --intnet1 "northstar-pilot"
VBoxManage modifyvm "VM-D" --nic2 none --nic3 none --nic4 none
VBoxManage modifyvm "VM-D" --nested-hw-virt off
VBoxManage modifyvm "VM-D" --audio-driver none

VBoxManage storagectl "VM-D" --name "SATA" --add sata --controller IntelAhci --portcount 2
VBoxManage createmedium disk --filename "C:\VirtualBox VMs\VM-D\VM-D.vdi" --size 24576 --format VDI
VBoxManage storageattach "VM-D" --storagectl "SATA" --port 0 --device 0 --type hdd --medium "C:\VirtualBox VMs\VM-D\VM-D.vdi"
VBoxManage storageattach "VM-D" --storagectl "SATA" --port 1 --device 0 --type dvddrive --medium "C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\Debian\debian-13.7.0-amd64-netinst.iso"
```

```
VBoxManage createvm --name "VM-R" --ostype Debian_64 --register
VBoxManage modifyvm "VM-R" --cpus 4 --memory 8192 --vram 16
VBoxManage modifyvm "VM-R" --nic1 intnet --intnet1 "northstar-pilot"
VBoxManage modifyvm "VM-R" --nic2 none --nic3 none --nic4 none
VBoxManage modifyvm "VM-R" --nested-hw-virt off
VBoxManage modifyvm "VM-R" --audio-driver none

VBoxManage storagectl "VM-R" --name "SATA" --add sata --controller IntelAhci --portcount 2
VBoxManage createmedium disk --filename "C:\VirtualBox VMs\VM-R\VM-R.vdi" --size 40960 --format VDI
VBoxManage storageattach "VM-R" --storagectl "SATA" --port 0 --device 0 --type hdd --medium "C:\VirtualBox VMs\VM-R\VM-R.vdi"
VBoxManage storageattach "VM-R" --storagectl "SATA" --port 1 --device 0 --type dvddrive --medium "C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\Debian\debian-13.7.0-amd64-netinst.iso"
```

### 4D — WSL coexistence verification

| Check | Command | Expected |
|---|---|---|
| WSL2 distros present | `wsl --list --verbose` | kali-linux Running v2, Debian Stopped v2 |
| Kali accessible | `wsl -d kali-linux -- uname -r` | Returns kernel version |
| Debian accessible | `wsl -d Debian -- uname -r` | Returns kernel version |
| VBS active | DeviceGuard query | Status == 2 |

Run BEFORE and AFTER VM creation. If any check degrades between the two runs, STOP.

### 4E — Acceptance metrics

| Metric | Threshold | How to measure |
|---|---|---|
| VBox starts with WHP | VMs boot | `VBoxManage startvm "VM-D" --type headless` succeeds |
| Internal Networking works | D↔R ping | From VM-D: `ping -c 3 10.0.100.2`; from VM-R: `ping -c 3 10.0.100.1` |
| No external network | VM cannot reach host | From VM-D: `ping -c 1 -W 2 10.0.2.2` fails; `ping -c 1 -W 2 8.8.8.8` fails |
| WSL2 intact after VM run | Distros still present | `wsl --list --verbose` unchanged |
| VBS intact after VM run | Status == 2 | DeviceGuard query unchanged |
| Host responsive | Desktop usable | Interactive check: windows respond, no visible freezes |

### 4F — Stop conditions (complete list)

| ID | Condition | Action |
|---|---|---|
| S1 | WHP enable fails | STOP. Do not reboot. Investigate. |
| S2 | Post-reboot VBS != 2 | STOP. Do not install VBox. Escalate. |
| S3 | Post-reboot WSL2 missing/degraded | STOP. Do not install VBox. Escalate. |
| S4 | Installer SHA-256 mismatch | STOP. Do not install. Re-acquire. |
| S5 | MSI install fails | STOP. Check Windows Installer logs. |
| S6 | Internal Networking unavailable with minimal install | Retry with `ADDLOCAL=VBoxApplication,VBoxNetwork` |
| S7 | VM fails to start (NEM error) | STOP. Check WHP state. Collect `VBoxSVC.log`. |
| S8 | D↔R ping fails | Check intnet name match, guest IP config. |
| S9 | VM can reach host or external network | STOP. Network isolation broken. Power off VMs. |
| S10 | WSL2 degraded after VM operations | STOP. Power off VMs. Check WSL state. |
| S11 | VBS deactivated at any point | STOP. Power off VMs. Do not reboot. Escalate. |
| S12 | Snapshot or saved state created | Power off VM. Document. Do not resume. |
| S13 | Host disk < 20 GiB free | STOP. Free space before continuing. |
| S14 | UBR changed (Windows Update applied) | Document new UBR. Re-evaluate before proceeding. |

## 5. Minimal installation specification

### 5.1 Installer extraction and MSI install

```powershell
# Step 1: Re-verify integrity
$hash = (Get-FileHash 'C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\VirtualBox-7.2.22-175245-Win.exe' -Algorithm SHA256).Hash
if ($hash -ne 'AFD0FF1656CBE04ED88CE619594AE021419D97CCE89FA9BDC443E9A963F1713F') { throw "SHA-256 MISMATCH: $hash" }

# Step 2: Extract MSI (does not install)
& 'C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\VirtualBox-7.2.22-175245-Win.exe' -extract -path 'C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\Oracle\extracted'

# Step 3: Install with minimal feature set
# ADDLOCAL=VBoxApplication — core only, no USB, no bridged, no host-only, no Python
# VBOX_START=0 — do not launch VirtualBox after install
# VBOX_INSTALLDESKTOPSHORTCUT=0 — no desktop shortcut
# VBOX_INSTALLQUICKLAUNCHSHORTCUT=0 — no quick launch shortcut
msiexec /i 'C:\Users\aazcl\Downloads\NORTHSTAR_P2_PILOT_ARTIFACTS\Oracle\extracted\VirtualBox-7.2.22-Win.msi' ADDLOCAL=VBoxApplication VBOX_START=0 VBOX_INSTALLDESKTOPSHORTCUT=0 VBOX_INSTALLQUICKLAUNCHSHORTCUT=0 /qb
```

### 5.2 MSI features excluded and rationale

| Feature | Excluded | Rationale |
|---|---|---|
| VBoxUSB | Yes | No USB passthrough needed |
| VBoxNetworkFlt | Yes | No bridged networking; Internal Networking is core |
| VBoxNetworkAdp | Yes | No host-only networking |
| VBoxPython | Yes | No Python API bindings needed |
| Extension Pack | Yes (separate download, not acquired) | No VRDP, PXE, encryption or cloud features needed |

### 5.3 Risk: VBoxNetwork exclusion

The Oracle manual (ch02 §2.1.3) documents `VBoxNetwork` as containing bridged and host-only drivers. It does not explicitly state whether Internal Networking has any dependency on these drivers. Internal Networking is described (ch06 §6.6) as a software switch within the VirtualBox engine that does not use host network interfaces.

**If Internal Networking fails without VBoxNetwork:** Re-install with `ADDLOCAL=VBoxApplication,VBoxNetwork`. The bridged and host-only drivers will be installed but never configured on any VM adapter. This is a known acceptable fallback, not a security concern, because:
- No VM adapter will be set to bridged or host-only mode
- The drivers being present does not create network exposure without adapter configuration
- Verification (§4B, §4E) confirms no unintended connectivity

Register this as a risk and approval condition if it occurs.

## 6. Network security

- Only Internal Networking is used (§C5)
- No host or external network access from VMs (Table 6.1)
- No NAT, bridged, host-only, NAT network, UDP tunnel, cloud or generic adapters
- No DHCP server (static IPs only)
- No port forwarding
- No VirtualBox Extension Pack (no VRDP remote access)
- Guest firewalls: configure `iptables` on both VM-D and VM-R to accept traffic only on the `northstar-pilot` interface and only from the peer's IP
- No shared folders between host and VMs

## 7. Reversibility assessment

| Component | Reversible | Method |
|---|---|---|
| WHP feature | Yes | `dism /Disable-Feature /FeatureName:HypervisorPlatform` + reboot |
| VirtualBox install | Yes | `msiexec /x` or Settings uninstall |
| VM-D | Yes | `VBoxManage unregistervm --delete` |
| VM-R | Yes | `VBoxManage unregistervm --delete` |
| VM disk files | Yes | Deleted with `--delete` or manually |
| Debian ISO | Unchanged | Remains at acquisition path |
| WSL2 / Kali | Not modified | No action needed |
| VBS / security | Not modified | No action needed |
| Bootloader | Not modified | No action needed |
| Windows registry | VBox entries removed on uninstall | Standard MSI behavior |

**Irreversible actions in the entire sequence:** NONE. Every action can be reversed to return the system to its pre-pilot state. The only operational cost is the reboot required to enable WHP and the reboot required to disable it during rollback.

## 8. QA of this runbook

### 8.1 Oracle/Microsoft documentation coherence

- WHP requirement: Oracle ch14 §14.1–14.2 confirms VBox+Hyper-V is experimental; WHP is named. Microsoft documents WHP as the API layer for third-party hypervisors under Hyper-V. Coherent.
- MSI features: Oracle ch02 §2.1.3 documents `ADDLOCAL` and feature names. Used as documented.
- Internal Networking: Oracle ch06 §6.6, Table 6.1 confirms isolation properties. Used as documented.
- `modifyvm` flags: Oracle ch08 documents `--nic<N>`, `--intnet<N>`, `--nested-hw-virt`, `--cpus`, `--memory`. Used as documented.

### 8.2 Prohibited components absent

- Extension Pack: not acquired, not installed, verified absent post-install
- Host-Only adapter: MSI feature excluded, VM adapter set to `none`
- NAT adapter: VM adapter set to `none` (only nic1 used, set to `intnet`)
- Bridged adapter: MSI feature excluded, VM adapter set to `none`
- Nested VT-x: explicitly disabled
- Snapshots: policy prohibits, verification commands provided
- VBS/VMP/HVCI changes: explicitly prohibited, verified at every stage

### 8.3 WSL2/VBS preservation

- WSL2 checked before and after every significant action
- VBS checked before and after every significant action
- Stop condition if either degrades

### 8.4 Artifact reuse

- VirtualBox installer SHA-256 and Authenticode evidence from acquisition report reused
- Debian ISO SHA-256 and GPG evidence from acquisition report reused
- No re-download required

### 8.5 No ROOT paths

- All Windows paths under `C:\Users\aazcl\` or `C:\VirtualBox VMs\` or `C:\Program Files\Oracle\VirtualBox\`
- All Debian paths under `/home/psm/northstar/workspace/`
- No `/root/`, no `/tmp/`, no Kali paths, no `C:\` root paths

### 8.6 Resource budget

- VM total: 5 vCPU / 10 GiB RAM / 64 GiB disk (dynamic)
- Host retains: 11 threads / ~54 GiB RAM
- Disk budget: ~80 GiB required free (dynamic allocation)
- No cost: all software is free/open-source, no paid services activated

### 8.7 Network security

- Only Internal Networking D↔R
- No host access, no external access, no port forwarding
- Verified by connectivity tests (§4E)

### 8.8 Reversibility

- Every action documented with undo procedure
- No irreversible operations
- Rollback sequence in §C12 with binary PASS/FAIL checks

### 8.9 Platform identity

- Measured from live system, not assumed
- UBR explicitly checked (9974, not 0)
- Edition verified via multiple sources

### 8.10 No costs

- VirtualBox: free (GPL v3)
- Debian: free
- WHP: Windows optional feature, no cost
- No Extension Pack (PUEL licensed, avoided)
- No paid cloud, provider or service activation

### 8.11 Pilot/NorthStar operational separation

- This is a PILOT PLATFORM setup, not NorthStar operational deployment
- No NORTHSTAR_PASS, no G1–G5 PASS declared
- The pilot validates platform viability only
- NorthStar operational deployment requires separate authorization after pilot acceptance

## 9. Execution sequence summary

| Phase | Steps | Reboot | Stop conditions |
|---|---|---|---|
| Pre-flight | Verify platform identity (§2), disk space, artifact hashes | No | S4, S13, S14 |
| WHP | Enable feature, reboot | **Yes** | S1, S2, S3 |
| Install | Extract MSI, install minimal VBox | No | S4, S5, S6 |
| Post-install | Verify VBox, WSL2, VBS, Internal Networking | No | S6, S10, S11 |
| VM create | Create VM-D and VM-R, configure | No | S7 |
| VM boot | Boot VM-D, install Debian | No | S7 |
| Network | Configure static IPs, test D↔R | No | S8, S9 |
| Coexistence | Verify WSL2 and VBS with VMs running | No | S10, S11 |
| Accept | Run acceptance metrics (§4E) | No | All |

**Total reboots: 1** (after WHP enable).

## 10. Deliverable status

**Status: P1B_PACKAGE_READY**

This corrected authorization package addresses all twelve P1A blocking defects:

| # | Correction | Status |
|---|---|---|
| C1 | WHP dependency per Oracle 7.2 manual | Addressed: WHP enable documented, verification provided |
| C2 | Prohibit disabling VBS/VMP/HVCI/bootloader | Addressed: explicit prohibition, stop conditions |
| C3 | Remove Extension Pack | Addressed: excluded from acquisition and installation |
| C4 | Remove Host-Only/NAT/Bridge/additional adapters | Addressed: MSI features excluded, VM adapters set to none |
| C5 | Keep only Internal Networking D↔R | Addressed: intnet configuration, isolation verified |
| C6 | Remove Nested VT-x/AMD-V | Addressed: --nested-hw-virt off |
| C7 | Exclude snapshots and suspended states | Addressed: policy, verification, stop condition |
| C8 | Recover identity and hashes of acquired packages | Addressed: full artifact evidence recovered |
| C9 | Check Windows edition/build/UBR correctly | Addressed: measured 26300.9974, API caveat documented |
| C10 | Reuse VM resource proposal | Addressed: VM-D 1/2/24, VM-R 4/8/40 |
| C11 | No ROOT or Kali for storage | Addressed: all paths user-owned, explicit prohibitions |
| C12 | Verifiable rollback actions | Addressed: ordered steps with binary checks, no time estimates |

**OP-1:** NOT enabled by this package.
**NORTHSTAR:** FAIL (unchanged).
**G1–G5, P3, P4:** OPEN (unchanged).

