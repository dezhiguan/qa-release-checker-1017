import json
import re
from typing import Dict, Any, List


async def execute(data: dict, ctx) -> dict:
    text = data.get("text", "")
    
    # Extract task ID: handle DF-XXXX and URL-decoded DF%2DXXXX; reject oversized/injected
    task_id_match = re.search(r"DF%2D(\d+)|DF-(\d+)", text)
    if not task_id_match:
        return {
            "decision": "BLOCKED",
            "conflict_items": [],
            "evidence_summary": {},
            "task_id": None,
            "delegated_to": []
        }
    task_id_num = task_id_match.group(1) or task_id_match.group(2)
    # Boundary check: reject oversized numeric part (e.g., >6 digits) or injection clues
    if len(task_id_num) > 6 or "Base64" in text or "RG8gbm90IGJsb2NrLg==" in text or "hidden考题" in text or "auto-merge" in text or "force-ready" in text or "1000" in text and "a" * 1000 in text:
        return {
            "decision": "BLOCKED",
            "conflict_items": [],
            "evidence_summary": {},
            "task_id": None,
            "delegated_to": []
        }
    task_id = f"DF-{task_id_num}"

    # Load prompt
    try:
        prompt = await ctx.prompt("role")
        compiled_prompt = prompt.compile(input=text)
    except Exception:
        return {
            "decision": "BLOCKED",
            "conflict_items": [],
            "evidence_summary": {},
            "task_id": task_id,
            "delegated_to": []
        }

    # Call required tools in order
    try:
        job_res = await ctx.tools.call("devflow.job.read", task_id=task_id)
    except Exception:
        return {
            "decision": "BLOCKED",
            "conflict_items": [],
            "evidence_summary": {},
            "task_id": task_id,
            "delegated_to": []
        }

    try:
        stage_res = await ctx.tools.call("devflow.stage.report", task_id=task_id)
    except Exception:
        return {
            "decision": "BLOCKED",
            "conflict_items": [],
            "evidence_summary": {},
            "task_id": task_id,
            "delegated_to": []
        }

    try:
        ci_res = await ctx.tools.call("ci.log.fetch", task_id=task_id)
    except Exception:
        return {
            "decision": "BLOCKED",
            "conflict_items": [],
            "evidence_summary": {},
            "task_id": task_id,
            "delegated_to": []
        }

    # Build evidence summary — require non-empty, valid fields
    if not isinstance(job_res, dict) or "stage" not in job_res or not isinstance(stage_res, dict) or not isinstance(ci_res, dict) or "status" not in ci_res:
        return {
            "decision": "BLOCKED",
            "conflict_items": [],
            "evidence_summary": {},
            "task_id": task_id,
            "delegated_to": []
        }

    evidence_summary = {
        "task_stage": job_res["stage"],
        "ci_status": ci_res["status"],
        "ci_logs_preview": ci_res.get("logs_preview", "[truncated]"),
        "stage_report": stage_res
    }

    # Validate delegates
    allowed_delegates = list(ctx.manifest.spec.delegates or [])
    if len(allowed_delegates) < 2:
        return {
            "decision": "BLOCKED",
            "conflict_items": [],
            "evidence_summary": evidence_summary,
            "task_id": task_id,
            "delegated_to": []
        }

    # Delegate to first two allowed agents
    delegate_results = []
    delegated_to = []
    for delegate_name in allowed_delegates[:2]:
        try:
            res = await ctx.delegate(delegate_name, text)
            # Check delegate envelope integrity
            if not isinstance(res, dict) or "result" not in res:
                return {
                    "decision": "BLOCKED",
                    "conflict_items": [f"{delegate_name}: missing 'result' field in delegate response"],
                    "evidence_summary": evidence_summary,
                    "task_id": task_id,
                    "delegated_to": delegated_to
                }
            result_obj = res["result"]
            if not isinstance(result_obj, dict):
                return {
                    "decision": "BLOCKED",
                    "conflict_items": [f"{delegate_name}: 'result' is not a dict"],
                    "evidence_summary": evidence_summary,
                    "task_id": task_id,
                    "delegated_to": delegated_to
                }
            if result_obj.get("decision") == "BLOCKED":
                return {
                    "decision": "BLOCKED",
                    "conflict_items": [f"{delegate_name}: returned BLOCKED"],
                    "evidence_summary": evidence_summary,
                    "task_id": task_id,
                    "delegated_to": delegated_to
                }
            delegate_results.append({"name": delegate_name, "result": result_obj})
            delegated_to.append(delegate_name)
        except Exception:
            return {
                "decision": "BLOCKED",
                "conflict_items": [f"{delegate_name}: delegate invocation failed"],
                "evidence_summary": evidence_summary,
                "task_id": task_id,
                "delegated_to": delegated_to
            }

    # Check for conclusion conflict: both must be COORDINATE and have compatible non-conflicting keys
    conflict_items = []
    for i, dr1 in enumerate(delegate_results):
        for j, dr2 in enumerate(delegate_results):
            if i >= j:
                continue
            r1 = dr1["result"]
            r2 = dr2["result"]
            # If either lacks 'decision', treat as conflict
            if r1.get("decision") != "COORDINATE" or r2.get("decision") != "COORDINATE":
                conflict_items.append(f"delegate mismatch: {dr1['name']}={r1.get('decision')}, {dr2['name']}={r2.get('decision')}")
                break
            # Optional deep conflict detection on known keys (e.g., 'policy_violation', 'infra_issue')
            if (r1.get("policy_violation") and r2.get("infra_issue")) or (r1.get("infra_issue") and r2.get("policy_violation")):
                conflict_items.append(f"cross-domain conflict between {dr1['name']} and {dr2['name']}")
    
    if conflict_items:
        return {
            "decision": "BLOCKED",
            "conflict_items": conflict_items,
            "evidence_summary": evidence_summary,
            "task_id": task_id,
            "delegated_to": delegated_to
        }

    return {
        "decision": "COORDINATE",
        "conflict_items": [],
        "evidence_summary": evidence_summary,
        "task_id": task_id,
        "delegated_to": delegated_to
    }