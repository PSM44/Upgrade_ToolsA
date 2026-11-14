"""P2-only real OpenCode editing boundary; never uses a desktop quota/runtime.

The trusted broker runs OpenCode in a separate mount namespace. Model tools may
read/edit only two staging files; generated code is executed by role QA sandboxes.
No live call is authorized by this module alone: the external runner owns gates.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

FILES = ("app.py", "test_app.py")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def policy(editor=True):
    rules = [{"action": "*", "resource": "*", "effect": "deny"}]
    for action in (("read", "edit") if editor else ()):
        for name in FILES:
            for resource in (name, "/work/" + name):
                rules.append({"action": action, "resource": resource, "effect": "allow"})
    rules.append({"action":"external_directory","resource":"*","effect":"deny"})
    agent = "northstar-p2-editor" if editor else "northstar-p2-review"
    return {"$schema": "https://opencode.ai/config.json", "permissions": rules,
            "policies":[{"action":a,"resource":"*","effect":"deny"} for a in ("mcp.use","skill.use","plugin.use","websearch.use")],
            "agents":{agent:{"description":"Bounded P2 task", "mode":"primary", "steps":8 if editor else 2,
                             "permissions":rules,"system":"Follow the bounded task. No credentials, external paths, delegation or shell."}}}

def check_regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("P2 requires regular, non-symlink files")

def app_path(broker, oid):
    if not SAFE_ID.fullmatch(oid) or ":" in oid or ".." in oid:
        raise ValueError("unsafe P2 objective id")
    base = broker.work_root.parent.resolve()
    path = base / "objectives" / oid / "app"
    for item in (base / "objectives", path.parent, path):
        if item.is_symlink():
            raise ValueError("P2 objective symlink forbidden")
    if not path.is_dir() or not path.resolve().is_relative_to(base):
        raise ValueError("P2 app outside owned runtime")
    for name in FILES:
        check_regular(path / name)
    return path

def command(binary, workspace, home, model, prompt, editor=True):
    return ["/usr/bin/bwrap", "--die-with-parent", "--new-session",
            "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
            "--unshare-cgroup", "--cap-drop", "ALL", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--ro-bind", "/etc/ssl", "/etc/ssl",
            "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
            "--ro-bind", str(binary), "/opt/opencode",
            "--bind", str(workspace), "/work", "--bind", str(home), "/home/p2",
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
            "--setenv", "HOME", "/home/p2", "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "XDG_DATA_HOME", "/home/p2/.local/share",
            "--setenv", "XDG_CONFIG_HOME", "/home/p2/.config",
            "--chdir", "/work", "/opt/opencode", "run", "--standalone",
            "--archived", "--format", "json", "--model", model, "--agent",
            "northstar-p2-editor" if editor else "northstar-p2-review", prompt]

def copy_account(destination):
    # Read-only byte copies; never open the account database via SQLite or mutate it.
    source_home = Path.home()
    for relative in (".local/share/opencode/opencode.db", ".config/opencode/service.json"):
        source = source_home / relative
        check_regular(source)
        if source.stat().st_mode & 0o077:
            raise PermissionError("OpenCode account file permissions too broad")
        wal = Path(str(source) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise RuntimeError("account snapshot requires quiescent empty WAL")
        before = digest(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(source.read_bytes()); target.chmod(0o600)
        if digest(source) != before or digest(target) != before:
            raise RuntimeError("account snapshot changed while copying")

def edit(broker, actor, request):
    if actor != "RORCH" or request.get("stage") not in {"BUILD", "FIX"}:
        raise PermissionError("P2 editing requires RORCH BUILD/FIX")
    if broker.routes.get("p2", {}).get("fixture_spec") != "retry_schedule_v1":
        raise PermissionError("P2 route policy not enabled")
    oid = str(request.get("objective_id", "")); rid = str(request["request_id"])
    stage = str(request["stage"]); prompt = str(request["prompt"])
    if rid != oid + (":rorch-build" if stage == "BUILD" else ":rorch-fix"):
        raise ValueError("P2 request/objective mismatch")
    app = app_path(broker, oid)
    route = broker.routes["roles"][actor]; model = str(route["model"])
    if route.get("runtime") != "opencode" or not model.startswith("opencode-go/"):
        raise PermissionError("P2 allows only the configured Go route, no fallback")
    limits = broker.routes["limits"]
    if limits.get("extra_usage") is not False or limits.get("available_credit_usd") != 0:
        raise PermissionError("P2 no-extra-usage policy missing")
    if len(prompt) > int(limits["max_prompt_chars"]):
        raise ValueError("prompt budget exceeded")
    fingerprint = hashlib.sha256(json.dumps({"oid":oid,"prompt":prompt,"op":"edit"},sort_keys=True).encode()).hexdigest()
    with sqlite3.connect(broker.db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT * FROM calls WHERE request_id=?", (rid,)).fetchone()
        if prior:
            if prior["actor"] != actor or prior["stage"] != stage or prior["input_sha256"] != fingerprint:
                raise RuntimeError("request id conflict")
            if prior["status"] != "COMPLETED":
                raise RuntimeError("P2 failed/in-flight invocation cannot be retried")
            result = json.loads(prior["response_json"])
            if {n:digest(app/n) for n in FILES} != result["files_sha256"]:
                raise RuntimeError("P2 replay artifact drift")
            return {"ok":True,"duplicate":True,"result":result,"receipt":broker.receipt(dict(prior))}
        if conn.execute("SELECT count(*) FROM calls").fetchone()[0] >= int(limits["max_calls"]):
            raise RuntimeError("model invocation budget exhausted")
        conn.execute("INSERT INTO calls(request_id,actor,stage,requested_model,status,input_sha256,created_at) VALUES(?,?,?,?,?,?,?)",(rid,actor,stage,model,"RUNNING",fingerprint,time.time()))
    private = broker.work_root / rid.replace(":", "_")
    try:
        private.mkdir(mode=0o700, exist_ok=False)
        workspace = private / "workspace"; workspace.mkdir(mode=0o700)
        home = private / "account"; home.mkdir(mode=0o700)
        before = {n:digest(app/n) for n in FILES}
        for name in FILES:
            shutil.copyfile(app/name, workspace/name)
        (workspace/"opencode.json").write_text(json.dumps(policy(),sort_keys=True))
        (workspace/"AGENTS.md").write_text("Edit only app.py and test_app.py. No shell, networking tools, credentials, plugins, Skills or delegation. The harness runs tests separately.\n")
        copy_account(home)
        started = time.time()
        proc = subprocess.run(command(Path(route["binary"]), workspace, home, model, prompt),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=min(int(route.get("timeout_seconds",180)),300), env={"PATH":"/usr/bin:/bin"})
        trace = private / "opencode.jsonl"
        trace.write_text(proc.stdout + proc.stderr); trace.chmod(0o600)
        events = []
        for line in proc.stdout.splitlines():
            try: event=json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(event,dict): events.append(event)
        edits = [e for e in events if e.get("type") == "tool_use" and e.get("part",{}).get("tool") in {"edit","write","patch","apply_patch"} and e.get("part",{}).get("state",{}).get("status") == "completed"]
        tool_events=[e for e in events if e.get("type")=="tool_use"]
        for event in tool_events:
            part=event.get("part",{}); state=part.get("state",{}); arguments=state.get("input") or {}
            if part.get("tool") not in {"read","edit","write","patch","apply_patch"} or state.get("status") != "completed":
                raise RuntimeError("OpenCode unexpected or failed tool call")
            if part.get("tool") in {"patch","apply_patch"}:
                patch_text=str(arguments.get("patchText",arguments.get("patch","")))
                targets=re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$",patch_text,re.M)
                if not targets or any(t not in {*FILES,*("/work/"+n for n in FILES)} for t in targets):
                    raise RuntimeError("OpenCode patch target outside scope")
            else:
                target=arguments.get("filePath",arguments.get("path",arguments.get("file_path")))
                if target not in {*FILES,*("/work/"+n for n in FILES)}:
                    raise RuntimeError("OpenCode tool target outside scope")
        if proc.returncode or not edits:
            raise RuntimeError("OpenCode did not prove a completed edit tool event")
        if set(p.name for p in workspace.iterdir()) != {*FILES,"opencode.json","AGENTS.md"}:
            raise RuntimeError("OpenCode changed files outside the two-file scope")
        for name in FILES: check_regular(workspace/name)
        after = {n:digest(workspace/n) for n in FILES}
        if after == before:
            raise RuntimeError("OpenCode produced no real code change")
        if {n:digest(app/n) for n in FILES} != before:
            raise RuntimeError("P2 delivery changed concurrently")
        for name in FILES: shutil.copyfile(workspace/name, app/name)
        finishes = [e.get("part",{}) for e in events if e.get("type")=="step_finish"]
        costs = [p.get("cost") for p in finishes]
        known = bool(costs) and all(type(v) in (int,float) and v >= 0 for v in costs)
        cost = sum(costs) if known else None
        tokens = {k:sum(int((p.get("tokens") or {}).get(k,0)) for p in finishes) for k in ("input","output")}
        result = {"edited":True,"files_sha256":after,"before_sha256":before,"edit_event_count":len(edits),"trace_sha256":digest(trace),"elapsed_seconds":time.time()-started,"model_identity":"configured_not_provider_attested"}
        response=json.dumps(result,sort_keys=True)
        with sqlite3.connect(broker.db) as conn:
            conn.execute("UPDATE calls SET status='COMPLETED',actual_provider=NULL,actual_model=NULL,output_sha256=?,input_tokens=?,output_tokens=?,total_tokens=?,cost_value=?,cost_status=?,cost_source=?,exit_code=0,completed_at=?,response_json=?,usage_json=? WHERE request_id=?",(hashlib.sha256(response.encode()).hexdigest(),tokens["input"],tokens["output"],sum(tokens.values()),cost,"provider_reported" if known else "unknown","opencode_step_finish" if known else "not_returned",time.time(),response,json.dumps({"step_count":len(finishes),"configured_model":model}),rid))
            conn.row_factory=sqlite3.Row
            row=dict(conn.execute("SELECT * FROM calls WHERE request_id=?",(rid,)).fetchone())
        return {"ok":True,"duplicate":False,"result":result,"receipt":broker.receipt(row)}
    except Exception as exc:
        with sqlite3.connect(broker.db) as conn:
            conn.execute("UPDATE calls SET status='FAILED',exit_code=-1,completed_at=?,error=? WHERE request_id=?",(time.time(),type(exc).__name__,rid))
        raise
