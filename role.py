import json
import re
from typing import Dict, Any, List


async def execute(data: dict, ctx) -> dict:
    # Extract task ID from input text using robust pattern (handles URL encoding, boundary cases)
    text = data.get("text", "")
    task_id_match = re.search(r"DF%2D(\d+)|DF-(\d+)", text)
    task_id = None
    if task_id_match:
        task_id = f"DF-{task_id_match.group(1) or task_id_match.group(2)}"
    
    # BLOCKED on missing task ID
    if not task_id:
        return {
            "decision": "BLOCKED",
            "reason": "No valid task ID found in input",
            "conflict_items": [],
            "evidence_summary": {}
        }

    # Load prompt
    try:
        prompt = await ctx.prompt("role")
        compiled_prompt = prompt.compile(input=text)
    except Exception:
        return {
            "decision": "BLOCKED",
            "reason": "Failed to load or compile role prompt",
            "conflict_items": [],
            "evidence_summary": {}
        }

    # Step 1: Read task metadata
    try:
        job_res = await ctx.tools.call("devflow.job.read", task_id=task_id)
    except Exception:
        return {
            "decision": "BLOCKED",
            "reason": "devflow.job.read failed",
            "conflict_items": [],
            "evidence_summary": {}
        }

    # Step 2: Read stage report
    try:
        stage_res = await ctx.tools.call("devflow.stage.report", task_id=task_id)
    except Exception:
        return {
            "decision": "BLOCKED",
            "reason": "devflow.stage.report failed",
            "conflict_items": [],
            "evidence_summary": {}
        }

    # Step 3: Fetch CI logs
    try:
        ci_res = await ctx.tools.call("ci.log.fetch", task_id=task_id)
    except Exception:
        return {
            "decision": "BLOCKED",
            "reason": "ci.log.fetch failed",
            "conflict_items": [],
            "evidence_summary": {}
        }

    # Collect evidence summary
    evidence_summary = {
        "task_stage": job_res.get("stage", "UNKNOWN"),
        "ci_status": ci_res.get("status", "UNKNOWN"),
        "ci_logs_preview": ci_res.get("logs_preview", "[truncated]"),
        "stage_report": stage_res
    }

    # Validate delegates
    allowed_delegates = list(ctx.manifest.spec.delegates or [])
    if len(allowed_delegates) < 2:
        return {
            "decision": "BLOCKED",
            "reason": "Insufficient registered delegates (need at least 2)",
            "conflict_items": [],
            "evidence_summary": evidence_summary
        }

    # Delegate to two authorized agents
    delegate_results = []
    for delegate_name in allowed_delegates[:2]:
        try:
            res = await ctx.delegate(delegate_name, text)
            delegate_results.append({"name": delegate_name, "result": res})
        except Exception as e:
            return {
                "decision": "BLOCKED",
                "reason": f"Delegate '{delegate_name}' invocation failed: {str(e)}",
                "conflict_items": [],
                "evidence_summary": evidence_summary
            }

    # Check for conflicts: any 'BLOCKED' result or mismatched conclusions
    conflict_items = []
    all_coordinated = True
    for dr in delegate_results:
        res_obj = dr["result"]
        if not isinstance(res_obj, dict):
            conflict_items.append(f"{dr['name']}: non-dict response")
            all_coordinated = False
        elif res_obj.get("decision") == "BLOCKED":
            conflict_items.append(f"{dr['name']}: BLOCKED conclusion")
            all_coordinated = False
        elif "conflict" in str(res_obj).lower():
            conflict_items.append(f"{dr['name']}: reported internal conflict")
            all_coordinated = False

    # Final decision
    if not all_coordinated:
        decision = "BLOCKED"
    else:
        decision = "COORDINATE"

    return {
        "decision": decision,
        "conflict_items": conflict_items,
        "evidence_summary": evidence_summary,
        "task_id": task_id,
        "delegated_to": [dr["name"] for dr in delegate_results]
    }