import sys
import os
import re
import json
from typing import Any, AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.workflow import Workflow, START, FunctionNode
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from google.genai import types

from .config import config

# Resolve the path to the MCP server
current_dir = os.path.dirname(os.path.abspath(__file__))
mcp_server_path = os.path.join(current_dir, "mcp_server.py")

# Define MCP Toolset
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[mcp_server_path],
        )
    )
)

# 1. Specialized LlmAgent 1: routine_med_manager
routine_med_manager = LlmAgent(
    name="routine_med_manager",
    model=config.model,
    instruction=(
        "You are an assistant specialized in managing daily routines and medication schedules for elderly patients.\n"
        "Use the available MCP tools to get the elderly status, update medication schedules, or log medication taken.\n"
        "If the user wants to update/modify medication details (such as dosage, frequency, or purpose), you must first check if 'approval_status' in the session state is 'approved'.\n"
        "If it is not approved:\n"
        "  - Do NOT call the update tool.\n"
        "  - Instead, write the requested action details to the session state under 'pending_action' as a dictionary, e.g., {'action': 'update_medication', 'medication': medication_name, 'dosage': dosage, 'frequency': frequency, 'purpose': purpose}.\n"
        "  - Set 'needs_approval' to True and 'approval_reason' to 'Changing medication dosage/frequency'.\n"
        "  - Return a message explaining that this change requires caregiver approval.\n"
        "If 'approval_status' is 'approved' and the pending action is what the user requested, call the tool to update the medication, and then clear the 'pending_action' and 'approval_status' from the state."
    ),
    tools=[mcp_toolset],
)

# 2. Specialized LlmAgent 2: wellbeing_log_analyst
wellbeing_log_analyst = LlmAgent(
    name="wellbeing_log_analyst",
    model=config.model,
    instruction=(
        "You are an assistant specialized in logging and analyzing well-being metrics and coordinating doctor visits/logs for elderly patients.\n"
        "Use the available MCP tools to view logs, log vitals (blood pressure, heart rate, temperature, symptoms), or book doctor appointments.\n"
        "If the user wants to book a doctor appointment, you must first check if 'approval_status' in the session state is 'approved'.\n"
        "If it is not approved:\n"
        "  - Do NOT call the book tool.\n"
        "  - Instead, write the requested action details to the session state under 'pending_action' as a dictionary, e.g., {'action': 'book_appointment', 'doctor': doctor_name, 'date_time': date_time, 'reason': reason}.\n"
        "  - Set 'needs_approval' to True and 'approval_reason' to 'Booking a new doctor visit/appointment'.\n"
        "  - Return a message explaining that this appointment booking requires caregiver approval.\n"
        "If 'approval_status' is 'approved' and the pending action is what the user requested, call the tool to book the appointment, and then clear the 'pending_action' and 'approval_status' from the state."
    ),
    tools=[mcp_toolset],
)

# 3. Orchestrator LlmAgent
orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=config.model,
    instruction=(
        "You are the main coordinator for the Elderly Care Assistant.\n"
        "Your job is to receive user requests (regarding medications, schedules, daily routines, vitals, well-being, or appointments) and delegate them to the appropriate specialist sub-agent:\n"
        "  - Use 'routine_med_manager' for medication schedules, logging meds taken, or modifying routines.\n"
        "  - Use 'wellbeing_log_analyst' for vital logs, symptoms, doctor appointments, or logs.\n"
        "If the specialist sub-agent indicates that caregiver approval is required (needs_approval), simply pass that information along.\n"
        "Always check the conversation history and state. If a caregiver just approved or denied a pending action (e.g. 'approval_status' is set to 'approved' or 'denied'), explain the final outcome of that decision."
    ),
    tools=[AgentTool(routine_med_manager), AgentTool(wellbeing_log_analyst)],
)

# 4. Security Checkpoint Function Node
def security_checkpoint(ctx: Context, node_input: Any) -> Event:
    text = ""
    if hasattr(node_input, "parts") and node_input.parts:
        text = "".join(part.text for part in node_input.parts if part.text)
    elif isinstance(node_input, str):
        text = node_input
    
    audit_log = {
        "event": "security_check",
        "input_length": len(text),
        "checks": {
            "pii_scrubbed": False,
            "injection_detected": False,
            "domain_policy_violated": False
        },
        "severity": "INFO"
    }

    # PII Scrubbing
    phone_pattern = r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
    
    scrubbed_text = text
    if re.search(phone_pattern, text):
        scrubbed_text = re.sub(phone_pattern, "[PHONE_REDACTED]", scrubbed_text)
        audit_log["checks"]["pii_scrubbed"] = True
    if re.search(ssn_pattern, text):
        scrubbed_text = re.sub(ssn_pattern, "[SSN_REDACTED]", scrubbed_text)
        audit_log["checks"]["pii_scrubbed"] = True

    # Prompt Injection Detection
    injection_keywords = ["ignore previous instructions", "bypass rules", "system prompt", "override instruction", "ignore instructions"]
    has_injection = any(kw in text.lower() for kw in injection_keywords)
    if has_injection:
        audit_log["checks"]["injection_detected"] = True
        audit_log["severity"] = "CRITICAL"
        print(json.dumps(audit_log))
        return Event(
            output="Security Violation: Potential prompt injection detected. Request blocked.",
            route="fail"
        )

    # Domain Policy / Harm Check
    harm_keywords = ["overdose", "poison", "lethal dose", "kill", "suicide"]
    has_harm = any(kw in text.lower() for kw in harm_keywords)
    if has_harm:
        audit_log["checks"]["domain_policy_violated"] = True
        audit_log["severity"] = "WARNING"
        print(json.dumps(audit_log))
        return Event(
            output="Safety Alert: Request contains reference to harmful actions. Request blocked and caregiver notified.",
            route="fail"
        )

    if audit_log["checks"]["pii_scrubbed"]:
        audit_log["severity"] = "WARNING"
        ctx.state["scrubbed_input"] = scrubbed_text
        print(json.dumps(audit_log))
        return Event(
            output=scrubbed_text,
            route="pass",
            state={"user_input": scrubbed_text}
        )

    print(json.dumps(audit_log))
    return Event(output=text, route="pass")

# 5. Routing Function Node
def route_decision(ctx: Context, node_input: Any) -> Event:
    needs_approval = ctx.state.get("needs_approval", False)
    if needs_approval:
        return Event(output=node_input, route="needs_approval")
    return Event(output=node_input, route="auto_approve")

# 6. Human Reviewer Node
async def human_reviewer(ctx: Context, node_input: Any) -> AsyncGenerator[Any, None]:
    if not ctx.resume_inputs:
        reason = ctx.state.get("approval_reason", "Action modification")
        pending = ctx.state.get("pending_action", {})
        msg = f"Caregiver Approval Required: {reason}.\nDetails: {json.dumps(pending, indent=2)}\nDo you approve? (yes/no)"
        yield RequestInput(interrupt_id="approve_action", message=msg)
        return
        
    user_approval = ctx.resume_inputs.get("approve_action", "")
    if user_approval.lower().strip() in ["yes", "y", "approve", "approved"]:
        ctx.state["needs_approval"] = False
        ctx.state["approval_status"] = "approved"
        yield Event(
            output="Caregiver approved the action. Re-processing request with approval...",
            state={"needs_approval": False, "approval_status": "approved"},
            route="approved"
        )
    else:
        ctx.state["needs_approval"] = False
        ctx.state["approval_status"] = "denied"
        ctx.state.pop("pending_action", None)
        yield Event(
            output="Caregiver denied the action. Operation cancelled.",
            state={"needs_approval": False, "approval_status": "denied"},
            route="denied"
        )

# 7. Final Output Node
async def final_output(ctx: Context, node_input: Any) -> AsyncGenerator[Any, None]:
    text = ""
    if hasattr(node_input, "parts") and node_input.parts:
        text = "".join(part.text for part in node_input.parts if part.text)
    elif isinstance(node_input, Event):
        if isinstance(node_input.output, str):
            text = node_input.output
        elif hasattr(node_input.output, "parts") and node_input.output.parts:
            text = "".join(part.text for part in node_input.output.parts if part.text)
    elif isinstance(node_input, str):
        text = node_input
        
    yield Event(
        content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
        output=text
    )

# Define Workflow Graph
root_agent = Workflow(
    name="elderly_care_workflow",
    edges=[
        ('START', security_checkpoint),
        (security_checkpoint, {"pass": orchestrator_agent, "fail": final_output}),
        (orchestrator_agent, route_decision),
        (route_decision, {"needs_approval": human_reviewer, "auto_approve": final_output}),
        (human_reviewer, {"approved": orchestrator_agent, "denied": final_output}),
    ]
)

# App instance
app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True)
)
