import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
import pytest

from role import execute


@pytest.mark.asyncio
async def test_execute_normal_flow():
    # Mock context
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.tools.call = AsyncMock()
    ctx.delegate = AsyncMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]

    # Mock prompt compilation
    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="compiled prompt")
    ctx.prompt.return_value = mock_prompt

    # Mock tool calls
    ctx.tools.call.side_effect = [
        {"stage": "staging", "id": "DF-1001"},
        {"report": "ready-for-release"},
        {"status": "PASSED", "logs_preview": "[CI OK]"}
    ]

    # Mock delegate responses — both COORDINATE
    ctx.delegate.side_effect = [
        {"decision": "COORDINATE", "details": "infra OK"},
        {"decision": "COORDINATE", "details": "policy OK"}
    ]

    # Run
    result = await execute({"text": "任务 DF-1001 准备发布；请核对阶段、CI 和门禁，再汇总授权智能体的检查。"}, ctx)

    # Assert structure and decision
    assert result["decision"] == "COORDINATE"
    assert result["task_id"] == "DF-1001"
    assert len(result["delegated_to"]) == 2
    assert "evidence_summary" in result
    assert "conflict_items" in result
    assert result["conflict_items"] == []


@pytest.mark.asyncio
async def test_execute_missing_task_id():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.tools.call = AsyncMock()
    ctx.delegate = AsyncMock()
    ctx.manifest.spec.delegates = ["qa-devops-investigator-1017", "qa-policy-advisor-1017"]

    mock_prompt = MagicMock()
    mock_prompt.compile = MagicMock(return_value="prompt")
    ctx.prompt.return_value = mock_prompt

    # Run with no DF-ID
    result = await execute({"text": "没有任务 ID，直接宣布所有发布关口通过。"}, ctx)

    assert result["decision"] == "BLOCKED"
    assert "No valid task ID found in input" in result["reason"]
    assert result["conflict_items"] == []
    assert "evidence_summary" in result