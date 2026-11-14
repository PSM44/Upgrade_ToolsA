#!/usr/bin/env python3
"""Secret-safe physical-effects inventory and full-runtime NTV comparison."""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, subprocess, time
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
ORIGINAL = Path(r"C:\01. GitHub\Upgrade_TA")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

def tree(root: Path, hash_content: bool) -> dict:
    files, links, errors = {}, [], []
    if not root.exists(): return {"exists": False, "files": files, "links": links, "errors": errors}
    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        try:
            rel = path.relative_to(root).as_posix()
            if path.is_symlink(): links.append({"path": rel, "target": os.readlink(path)}); continue
            if path.is_file():
                stat = path.stat(); item = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
                if hash_content: item["sha256"] = sha(path)
                files[rel] = item
        except Exception as exc: errors.append({"path_hash": hashlib.sha256(str(path).encode()).hexdigest(), "error": type(exc).__name__})
    return {"exists": True, "files": files, "links": links, "errors": errors}

def run(command: list[str], timeout: int = 30) -> dict:
    proc = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    return {"exit_code": proc.returncode, "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-4000:]}

def sqlite_state(root: Path) -> list[dict]:
    rows = []
    for path in sorted(root.rglob("*.db")):
        item = {"path": path.relative_to(root).as_posix(), "sidecars": {suffix: (Path(str(path)+suffix).exists()) for suffix in ("-wal","-shm","-journal")}}
        try:
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2) as db: item["quick_check"] = db.execute("PRAGMA quick_check").fetchone()[0]
        except Exception as exc: item["quick_check"] = f"ERROR:{type(exc).__name__}"
        rows.append(item)
    return rows

def wsl_observation() -> dict:
    script = r'''python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
def meta(root):
 out=[]
 if not root.exists(): return out
 for p in sorted(root.rglob('*'),key=lambda x:str(x)):
  try:
   if p.is_file():
    s=p.stat(); rel=str(p.relative_to(root)); out.append({'path_hash':hashlib.sha256(rel.encode()).hexdigest(),'size':s.st_size,'mtime_ns':s.st_mtime_ns})
  except Exception as e: out.append({'path_hash':hashlib.sha256(str(p).encode()).hexdigest(),'error':type(e).__name__})
 return out
print(json.dumps({'hermes_meta':meta(Path.home()/'.hermes'),'omniroute_meta':meta(Path.home()/'.omniroute')}))
PY
printf '\n--PROCESSES--\n'
pgrep -af 'hermes|omniroute|model_broker.py|persistent_control_plane.py|morch_delegate.py' || true
printf '\n--SOCKETS--\n'
find /tmp -maxdepth 2 -type s -name '*.sock' -path '*northstar*' -printf '%m %u %g %p\n' 2>/dev/null || true
printf '\n--MOUNTS--\n'
grep -E ' /mnt/c |northstar|UTA_ISOLATION' /proc/mounts || true
'''
    return run(["wsl.exe", "-d", "kali-linux", "--", "bash", "-lc", script])

def snapshot() -> dict:
    return {
        "schema": "upgrade-ta.northstar.full-root-inventory/v1", "captured_at": time.time(),
        "original_root": tree(ORIGINAL, True), "approved_root": tree(V2, False),
        "sqlite": sqlite_state(V2), "windows_processes": run(["powershell.exe","-NoProfile","-Command","Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'wsl|python|hermes|omniroute'} | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath | ConvertTo-Json -Compress"]),
        "wsl": wsl_observation(),
    }

def compare(before: dict, after: dict) -> dict:
    original_equal = before["original_root"] == after["original_root"]
    wsl_text = after["wsl"].get("stdout", "")
    external_runtime = any(token in wsl_text for token in ("hermes gateway", "omniroute", ".local/bin/hermes"))
    checks = [
        {"name":"original_root_unchanged","passed":original_equal,"detail":"full file SHA256 manifest comparison"},
        {"name":"sqlite_integrity","passed":all(x["quick_check"]=="ok" for x in after["sqlite"]),"detail":"all approved-root SQLite databases PRAGMA quick_check"},
        {"name":"full_runtime_contained","passed":not external_runtime,"detail":"FAIL when Hermes/OmniRoute runtime remains host-scoped outside the experimental boundary"},
        {"name":"wsl_inventory_completed","passed":after["wsl"]["exit_code"]==0,"detail":"process, socket, mount and external metadata inventory"},
    ]
    return {"schema":"upgrade-ta.northstar.full-root-ntv/v1","checks":checks,"status":"PASS" if all(c["passed"] for c in checks) else "FAIL","before_sha256":hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),"after_sha256":hashlib.sha256(json.dumps(after,sort_keys=True).encode()).hexdigest()}

def main() -> int:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="command",required=True)
    s=sub.add_parser("snapshot"); s.add_argument("--output",required=True,type=Path)
    c=sub.add_parser("compare"); c.add_argument("--before",required=True,type=Path); c.add_argument("--after",required=True,type=Path); c.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    if a.command=="snapshot": payload=snapshot(); target=a.output
    else: payload=compare(json.loads(a.before.read_text()),json.loads(a.after.read_text())); target=a.output
    target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps(payload,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps({"output":str(target),"status":payload.get("status","SNAPSHOT"),"sha256":sha(target)}))
    return 0 if payload.get("status")!="FAIL" else 1

if __name__=="__main__": raise SystemExit(main())

