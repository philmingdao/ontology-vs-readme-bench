"""Multi-arm OpenAI-compatible tool-calling agent runner + offline mock simulator."""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    DefaultHttpxClient,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from bench.evaluator import EvalScore, evaluate_submission
from bench.owl_engine import EnterpriseOntologyEngine
from bench.tasks import TaskSpec, compute_ground_truth
from bench.tools import ToolExecutor, build_system_prompt, get_tool_schemas


@dataclass
class RunTrace:
    arm: str
    task_id: str
    tier: int
    run_index: int
    model: str
    passed: bool
    score: float
    reason: str
    decision_exact: bool | None
    rules_exact: bool | None
    rules_f1: float | None
    submitted_answer: Any
    gold_answer: Any
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    turns: int
    tool_calls: int
    tool_call_breakdown: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    mock: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _try_parse_json_fallback(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None
    if "```" in stripped:
        parts = stripped.split("```")
        for p in parts:
            cand = p.strip()
            if cand.startswith("json"):
                cand = cand[4:].strip()
            if cand.startswith("{") or cand.startswith("["):
                try:
                    return json.loads(cand)
                except Exception:
                    pass
    try:
        return json.loads(stripped)
    except Exception:
        return None


def _has_socks_proxy_env() -> bool:
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        if "socks" in os.environ.get(var, "").lower():
            return True
    return False


class OpenAIAgentRunner:
    """Runs a real OpenAI-compatible tool-calling loop for one (arm, task, run_index)."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None,
        model: str,
        temperature: float = 0.0,
        max_turns: int = 12,
        max_retries: int = 5,
    ) -> None:
        http_client = DefaultHttpxClient(trust_env=False, timeout=120.0) if _has_socks_proxy_env() else None
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=120.0,
            http_client=http_client,
        )
        self.model = model
        self.temperature = temperature
        self.max_turns = max_turns
        self.max_retries = max_retries

    def _chat_with_retry(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=self.temperature,
                )
            except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError) as exc:
                last_exc = exc
                if attempt == self.max_retries - 1:
                    raise
                delay = (2.0 ** (attempt + 1)) + random.uniform(0.1, 1.0)
                time.sleep(delay)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Unexpected retry exit")

    def run_task(
        self,
        arm: str,
        task: TaskSpec,
        run_index: int,
        db_path: Path,
        semantics_dir: Path,
        owl_engine: EnterpriseOntologyEngine | None = None,
        owl_lock: threading.Lock | None = None,
    ) -> RunTrace:
        t0 = time.perf_counter()
        system_prompt = build_system_prompt(arm, db_path, semantics_dir)
        tools = get_tool_schemas(arm)
        executor = ToolExecutor(db_path=db_path, arm=arm, owl_engine=owl_engine, owl_lock=owl_lock)
        gold = compute_ground_truth(db_path, task)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"[{task.task_id}] {task.question}"},
        ]

        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        turns = 0
        tool_calls_count = 0
        breakdown: dict[str, int] = {}
        submitted: Any = None
        error_msg: str | None = None
        nudged = False

        try:
            for _ in range(self.max_turns):
                turns += 1
                resp = self._chat_with_retry(messages=messages, tools=tools)
                if getattr(resp, "usage", None) is not None:
                    prompt_tokens += int(getattr(resp.usage, "prompt_tokens", 0) or 0)
                    completion_tokens += int(getattr(resp.usage, "completion_tokens", 0) or 0)
                    total_tokens += int(getattr(resp.usage, "total_tokens", 0) or 0)

                msg = resp.choices[0].message
                msg_dict: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
                reasoning_content = getattr(msg, "reasoning_content", None)
                if reasoning_content is None and getattr(msg, "model_extra", None):
                    reasoning_content = msg.model_extra.get("reasoning_content")
                if reasoning_content is not None:
                    msg_dict["reasoning_content"] = reasoning_content
                if msg.tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                messages.append(msg_dict)

                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        fn_name = tc.function.name
                        tool_calls_count += 1
                        breakdown[fn_name] = breakdown.get(fn_name, 0) + 1
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                            if not isinstance(args, dict):
                                args = {"value": args}
                        except Exception as exc:
                            args = {}
                            out_str = json.dumps({"error": f"Invalid JSON arguments: {exc}"})
                            sub = None
                        else:
                            out_str, sub = executor.execute(fn_name, args)

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": out_str,
                            }
                        )
                        if sub is not None:
                            submitted = sub

                    if submitted is not None:
                        break
                else:
                    parsed = _try_parse_json_fallback(msg.content or "")
                    if parsed is not None:
                        submitted = parsed
                        break
                    if not nudged:
                        nudged = True
                        messages.append(
                            {
                                "role": "user",
                                "content": "Please call the `submit_final_answer` tool now with your final JSON answer.",
                            }
                        )
                    else:
                        submitted = msg.content
                        break
        except Exception as exc:
            error_msg = str(exc)

        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens

        ev: EvalScore = evaluate_submission(task, submitted, gold)
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        return RunTrace(
            arm=arm,
            task_id=task.task_id,
            tier=task.tier,
            run_index=run_index,
            model=self.model,
            passed=ev.passed if error_msg is None else False,
            score=ev.score if error_msg is None else 0.0,
            reason=error_msg or ev.reason,
            decision_exact=ev.decision_exact,
            rules_exact=ev.rules_exact,
            rules_f1=ev.rules_f1,
            submitted_answer=submitted,
            gold_answer=gold,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            turns=turns,
            tool_calls=tool_calls_count,
            tool_call_breakdown=breakdown,
            latency_ms=latency_ms,
            mock=False,
            error=error_msg,
        )


class MockAgentRunner:
    """Offline deterministic simulator exercising full eval -> trace -> report pipeline."""

    # Calibrated synthetic pass probabilities reflecting typical empirical profile
    _PASS_PROBS: dict[tuple[str, int], float] = {
        ("bare_rds", 1): 0.95,
        ("bare_rds", 2): 0.20,
        ("bare_rds", 3): 0.20,
        ("bare_rds", 4): 0.10,
        ("readme_md", 1): 1.00,
        ("readme_md", 2): 0.90,
        ("readme_md", 3): 0.85,
        ("readme_md", 4): 0.85,
        ("raw_owl", 1): 1.00,
        ("raw_owl", 2): 0.80,
        ("raw_owl", 3): 0.75,
        ("raw_owl", 4): 0.80,
        ("owl_reasoner", 1): 1.00,
        ("owl_reasoner", 2): 0.85,
        ("owl_reasoner", 3): 0.95,
        ("owl_reasoner", 4): 1.00,
    }

    def __init__(self, model: str = "mock-simulator-v1") -> None:
        self.model = model

    def run_task(
        self,
        arm: str,
        task: TaskSpec,
        run_index: int,
        db_path: Path,
        semantics_dir: Path,
        owl_engine: EnterpriseOntologyEngine | None = None,
        owl_lock: threading.Lock | None = None,
    ) -> RunTrace:
        gold = compute_ground_truth(db_path, task)
        sys_prompt = build_system_prompt(arm, db_path, semantics_dir)
        base_prompt_tokens = max(400, int(len(sys_prompt) / 3.8))

        digest = hashlib.sha256(f"{arm}:{task.task_id}:{run_index}".encode()).hexdigest()
        rng = random.Random(int(digest[:16], 16))
        p_pass = self._PASS_PROBS[(arm, task.tier)]
        will_pass = rng.random() < p_pass

        if arm == "owl_reasoner" and task.tier == 4 and owl_engine is not None:
            assert task.po_req_id is not None
            shacl_out = owl_engine.validate_po_shacl(task.po_req_id)
            submitted = {
                "decision": shacl_out["decision"],
                "violated_rules": shacl_out["violated_rules"],
            }
            will_pass = True
        elif will_pass:
            if task.answer_type in ("scalar_number", "scalar_string"):
                submitted = {"value": gold}
            elif task.answer_type == "table":
                submitted = {"columns": list(task.expected_columns), "rows": gold}
            else:
                submitted = gold
        else:
            if task.answer_type == "scalar_number":
                submitted = {"value": round(float(gold) * 1.17 + 42.0, 2)}
            elif task.answer_type == "scalar_string":
                submitted = {"value": "WRONG_ID"}
            elif task.answer_type == "table":
                submitted = {"columns": list(task.expected_columns), "rows": gold[:-1] if len(gold) > 1 else []}
            else:
                # Simulate common Tier-4 error: wrong decision or missed rule
                exp_dec = gold["decision"]
                submitted = {
                    "decision": "APPROVE" if exp_dec != "APPROVE" else "REJECT",
                    "violated_rules": [] if exp_dec != "APPROVE" else ["R22"],
                }

        ev = evaluate_submission(task, submitted, gold)
        turns = rng.randint(2, 4) if task.tier <= 2 else rng.randint(3, 5)
        if arm == "owl_reasoner" and task.tier >= 3:
            breakdown = {"owl_validate_po_shacl": 1} if task.tier == 4 else {"owl_list_classes_and_members": 1, "run_sql": 1}
        else:
            breakdown = {"run_sql": max(1, turns - 1)}
        breakdown["submit_final_answer"] = 1

        prompt_toks = base_prompt_tokens * turns + rng.randint(150, 500)
        comp_toks = turns * rng.randint(120, 260)

        return RunTrace(
            arm=arm,
            task_id=task.task_id,
            tier=task.tier,
            run_index=run_index,
            model=self.model,
            passed=ev.passed,
            score=ev.score,
            reason=ev.reason,
            decision_exact=ev.decision_exact,
            rules_exact=ev.rules_exact,
            rules_f1=ev.rules_f1,
            submitted_answer=submitted,
            gold_answer=gold,
            prompt_tokens=prompt_toks,
            completion_tokens=comp_toks,
            total_tokens=prompt_toks + comp_toks,
            turns=turns,
            tool_calls=sum(breakdown.values()),
            tool_call_breakdown=breakdown,
            latency_ms=round(rng.uniform(45.0, 180.0), 2),
            mock=True,
            error=None,
        )
