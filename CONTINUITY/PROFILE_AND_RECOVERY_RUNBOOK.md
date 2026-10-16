# DSH / OmniRoute Profile and Recovery Runbook
Canonical DSH version `0.2.0-rc.2`; real launcher `DSH2--`, not raw legacy `/usr/local/bin/dsh`. Launcher: `/home/aazcl/.local/bin/DSH2--`, effective `DSH_HOME=$HOME/.dsh-prod-0.2.0-rc.2`, web 3080. OmniRoute `3.8.50`, existing ports 20128/20131/20132. Do not edit launcher during UTA021.
## Inspection (read-only)
`command -v dsh; dsh --version; command -v pnpm; pnpm --version; sed -n '1,18p' ~/.local/bin/DSH2--`
`DSH_HOME="$HOME/.dsh-prod-0.2.0-rc.2" dsh plugin --profile web ls --depth 0`
`ps -eo pid,args | grep -E '[d]sh.*web'`
## Recovery
Keep an external durable backup of effective DSH profile, launcher, current metadata and before/after hashes. Never automatically overwrite running profile. Compare effective and default `~/.dsh` profiles; former is authoritative for DSH2--. Stop and request review on port conflict, version mismatch, plugin peer error or credential exposure. A 401 DSH Web request may be normal authentication boundary. OmniRoute health is not proof of a live model route.
## Evidence rule
Package present, plugin loaded, UI visible, model/tool actually invoked and end-to-end verified are separate states. No assumed paid testing.
