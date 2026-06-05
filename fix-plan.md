# Bug Fix Plan — 7 Issues from Judge's Test Report

## Root Cause Analysis Summary

After reading the full codebase, here are the confirmed root causes:

---

### Bug #1 (P0): full_remediation verify 100% failure — **3 root causes found**

**Root Cause A — Repair target extraction fails:**
- Monitor anomaly detection (`monitor_agent/agent.py:100`) produces anomalies with only `{metric, value, threshold, severity}` — NO `link_id` or `device_id`
- Diagnoser (`diagnoser_agent/agent.py:180`) returns `{fault_type, confidence, description, repair_action}` — NO `context.target`
- Repairer's `_extract_target()` (`repairer_agent/agent.py:129-143`) falls back to `f"device-{domain}"` = `"device-east-china"`, which doesn't match any link key in simulator state
- `_apply_traffic_shaping("device-east-china", ...)` → `state.get_link("device-east-china")` returns `None` → repair is a no-op → fault remains → verify fails

**Root Cause B — TOOL_EXECUTE_REPAIR sends wrong body format:**
- `tool_client.py:86`: `call_tool(TOOL_EXECUTE_REPAIR, {"action_type": ..., "target": ..., "params": ...})` sends JSON body `{"action_type":"traffic_shape","target":"...","params":{...}}`
- `api.py:91`: `apply_repair(action: dict)` expects top-level fields: `action.get("action_type")` — this actually works since FastAPI deserializes the JSON body as the `action` parameter
- But the `params` dict gets passed to `handler(target, **params)`, meaning `max_bandwidth` and `backup_link_id` become keyword args — this matches the function signature, so it actually works IF the target is correct

**Root Cause C — No early exit when no fault detected:**
- When fault_type is "none", the full_remediation DAG still runs repair→verify→report
- Repair skips execution (no active faults), verify checks metrics, finds no improvement, retries 3 times, then fails

**Fix Plan:**
1. **Fix `_extract_target`**: Have repair agent query active faults via `TOOL_LIST_FAULTS` and use the fault's `target` field as the repair target
2. **Add fault_type="none" early exit**: In repair agent, if fault_type is "none", return success immediately. In verify agent, if no repair was needed, return pass immediately.
3. **Enrich anomaly data**: Add `link_id` field to monitor anomalies so downstream can reference specific links

---

### Bug #2 (P0): NL instructions create no DAG feedback

**Root Cause:**
- The orchestrator handles NL messages correctly and submits DAGs via HTTP POST to `/dag`
- The GUI polls `/dag` for 15 seconds looking for new DAGs
- The polling check at line 368: `dags.length > beforeDags` — but `loadDags()` fetches DAGs and renders them, then `dagData` gets populated
- The issue is likely that the orchestrator takes >15s to process the NL command (LangGraph workflow + LLM call), and by the time the DAG is created, the polling has timed out
- OR: the `/messages` endpoint returns immediately but the orchestrator processes async, and there's a race condition

**Fix Plan:**
1. Add immediate feedback when NL message is received (return the dag_id in the `/messages` response if possible)
2. Increase polling timeout from 15s to 30s
3. Add a "processing" indicator while waiting

---

### Bug #3 (P0): 7 zombie DAGs stuck in "running" for 8+ hours

**Root Cause:**
- No timeout mechanism in the scheduler
- DAGs submitted before a server restart stay in "running" forever
- `timeout_ms` field exists in DAG nodes but is never checked

**Fix Plan:**
1. Add a stale DAG cleanup loop in the scheduler's `_schedule_loop()`:
   - Check all running DAGs
   - If any DAG has been running > `timeout_ms * (1 + max_retries) + 60s`, mark it as failed
   - On startup, scan and mark stale running DAGs as failed
2. Add a "clear stale" API endpoint for manual cleanup

---

### Bug #4 (P1): Reporter says "repair failed" in no-fault scenarios

**Root Cause:**
- `reporter_agent/agent.py:101`: `repair_success = repair_result.get("status") == "ok"`
- When fault_type is "none", repair returns `{"status": "ok", "skipped": True}`, so `repair_success` is `True`
- But the narrative builder at line 163-164: if `repair_success` is True, says "修复操作已成功执行" — which is confusing when no repair was needed
- The LLM enhancement might override this with incorrect text

**Fix Plan:**
1. Check if repair was skipped (`repair_result.get("skipped")`)
2. When skipped, narrative should say "系统正常，无需修复" instead of "修复操作已成功执行"
3. Handle `fault_type="none"` specially in the narrative

---

### Bug #5 (P1): DDoS fault injection shows no visible metric change

**Root Cause:**
- DDoS injection sets `fault_bandwidth_util = 0.99`, `fault_packet_loss = 0.30`, `fault_latency = ~500ms` on connected links
- But the dashboard displays **aggregated domain metrics** from `/simulator/metrics`, which averages all links in the domain
- If the domain has 3 servers and only 1 link is affected, the average might be: `(0.99 + 0.40 + 0.40) / 3 = 0.60` for bandwidth_util — above threshold but not dramatic
- For packet_loss: `(0.30 + 0.001 + 0.001) / 3 = 0.10` — should be visible
- For latency: `(500 + 10 + 10) / 3 = 173ms` — should be visible

Actually, the aggregator in `generator.py:115-122` uses `get_effective_*()` methods which respect fault overrides. So the aggregated values SHOULD show anomalies. But the thresholds in the GUI might not highlight them. Let me check the GUI metric display thresholds.

Actually, looking at the code more carefully, the DDoS targets a DEVICE (Edge-R2), not a specific link. The injection iterates `get_all_links()` and checks `link.from_node == target_id or link.to_node == target_id`. This should affect all links connected to Edge-R2. In the topology, Edge-R2 connects to its servers. If there are 3 server links and all are affected, the average should be dramatic.

The issue might be that the GUI dashboard doesn't refresh fast enough, or the metric display thresholds don't show "warn" or "critical" styling.

**Fix Plan:**
1. Check GUI metric thresholds and styling
2. Ensure DDoS injection affects metrics prominently
3. Add a visual indicator (topology node color change) when fault is active

---

### Bug #6 (P1): Compound fault button doesn't work

**Root Cause:**
- The button calls `runMultiFaultDemo()` which calls `clearAllFaults()` → `demoFault()` × 3 → `sendNL()`
- `clearAllFaults()` calls `getJSON(SIM + '/simulator/fault/clear_all')` — this is a GET request
- The endpoint `GET /simulator/fault/clear_all` returns `{"status": "ok"}`
- `demoFault()` uses `fetch(..., {method:'POST'})` to inject faults
- If any step throws an error, the whole chain breaks silently

The most likely issue: `clearAllFaults()` might fail if the simulator isn't responding, or `demoFault()` fails for one of the fault types (e.g., if `link_congestion` target format is wrong).

**Fix Plan:**
1. Add error handling and logging to `runMultiFaultDemo()`
2. Each fault injection should be independent (try/catch each one)
3. Show progress feedback in the log

---

### Bug #7 (P2): DAG description truncation + Agent load always 0%

**Root Cause A — DAG description truncation:**
- The DAG description comes from the orchestrator's `template_full_remediation` which generates short descriptions
- But when LangGraph workflow is used, it might prepend "LangGraph " to the description
- The card CSS `.dag-desc` has no `text-overflow: ellipsis` but the card width is narrow

**Root Cause B — Agent load always 0%:**
- `store.py:36`: `profile.load` is stored at registration time
- `heartbeat()` only updates `last_heartbeat_ms` and `status`, never `load`
- Agents always register with `load=0` and it's never updated

**Fix Plan:**
1. Fix DAG description: make cards wider or use `text-overflow: visible` / tooltip
2. Fix agent load: update `load` during heartbeat based on active task count, or calculate from DAG assignments

---

## Implementation Order

1. **Bug #1** (P0) — Fix verify failure (biggest impact, core feature)
2. **Bug #3** (P0) — Stale DAG cleanup (quick win, high visibility)
3. **Bug #4** (P1) — Reporter narrative fix
4. **Bug #5** (P1) — DDoS metrics visibility
5. **Bug #6** (P1) — Compound fault button
6. **Bug #2** (P0) — NL feedback
7. **Bug #7** (P2) — UI fixes
