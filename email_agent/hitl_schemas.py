"""Human-in-the-Loop (HITL) schemas for Agent Inbox compatibility.

These schemas match the Agent Inbox expected format for interrupts and responses.
See: https://github.com/langchain-ai/agent-inbox

Usage:
    from email_agent.hitl_schemas import HumanInterrupt, HumanResponse, create_interrupt
    
    # Create an interrupt for user approval
    interrupt_data = create_interrupt(
        action="send-mail",
        args={"to": "user@example.com", "subject": "Hello"},
        description="Send email to user@example.com"
    )
"""

from typing import Any, Dict, Literal, Optional, Union

from typing_extensions import TypedDict


class ActionRequest(TypedDict):
    """Request for an action to be performed."""
    action: str  # Tool name
    args: Dict[str, Any]  # Tool arguments


class HumanInterruptConfig(TypedDict):
    """Configuration for what actions the human can take."""
    allow_ignore: bool  # Can the user skip this action?
    allow_respond: bool  # Can the user provide a free-text response?
    allow_edit: bool  # Can the user modify the args?
    allow_accept: bool  # Can the user accept as-is?


class HumanInterrupt(TypedDict):
    """Interrupt data sent to the UI for human review."""
    action_request: ActionRequest
    config: HumanInterruptConfig
    description: Optional[str]  # Human-readable description of the action


class HumanResponse(TypedDict):
    """Response from the human after reviewing an interrupt.
    
    Response types:
    - "accept": Execute the action with original args
    - "edit": Execute with modified args (args field contains new ActionRequest)
    - "response": User provided a free-text response (args field contains the text)
    - "ignore": Skip this action entirely
    """
    type: Literal["accept", "ignore", "response", "edit"]
    args: Union[None, str, ActionRequest]  # Depends on response type


# Default configurations for different tool types
HITL_CONFIGS = {
    # Email sending - allow all options
    "send-mail": HumanInterruptConfig(
        allow_ignore=True,
        allow_respond=True,
        allow_edit=True,
        allow_accept=True
    ),
    # Calendar events - allow all options
    "create-calendar-event": HumanInterruptConfig(
        allow_ignore=True,
        allow_respond=True,
        allow_edit=True,
        allow_accept=True
    ),
    "create-specific-calendar-event": HumanInterruptConfig(
        allow_ignore=True,
        allow_respond=True,
        allow_edit=True,
        allow_accept=True
    ),
    # Question tool - only respond or ignore
    "Question": HumanInterruptConfig(
        allow_ignore=True,
        allow_respond=True,
        allow_edit=False,
        allow_accept=False
    ),
    # Default for other HITL tools
    "default": HumanInterruptConfig(
        allow_ignore=True,
        allow_respond=True,
        allow_edit=True,
        allow_accept=True
    )
}


def get_hitl_config(tool_name: str) -> HumanInterruptConfig:
    """Get the HITL configuration for a tool.
    
    Args:
        tool_name: Name of the tool
        
    Returns:
        HumanInterruptConfig for the tool
    """
    return HITL_CONFIGS.get(tool_name, HITL_CONFIGS["default"])


def create_interrupt(
    action: str,
    args: Dict[str, Any],
    description: Optional[str] = None,
    config: Optional[HumanInterruptConfig] = None
) -> HumanInterrupt:
    """Create a HumanInterrupt for sending to the UI.
    
    Args:
        action: Tool name
        args: Tool arguments
        description: Human-readable description (auto-generated if not provided)
        config: HITL config (uses default for tool if not provided)
        
    Returns:
        HumanInterrupt ready to pass to interrupt()
    """
    if config is None:
        config = get_hitl_config(action)
    
    if description is None:
        description = _generate_description(action, args)
    
    return HumanInterrupt(
        action_request=ActionRequest(action=action, args=args),
        config=config,
        description=description
    )


def _generate_description(action: str, args: Dict[str, Any]) -> str:
    """Generate a human-readable description of an action.
    
    Args:
        action: Tool name
        args: Tool arguments
        
    Returns:
        Human-readable description
    """
    if action == "send-mail":
        to = args.get("to", args.get("toRecipients", "unknown"))
        subject = args.get("subject", "no subject")
        return f"Send email to {to}: \"{subject}\""
    
    elif action in ("create-calendar-event", "create-specific-calendar-event"):
        subject = args.get("subject", "Untitled event")
        start = args.get("start", args.get("startDateTime", "unknown"))
        return f"Create calendar event: \"{subject}\" at {start}"
    
    elif action == "Question":
        question = args.get("question", "")
        return f"Agent is asking: {question}"
    
    elif action == "manage_email":
        request = args.get("request", "")[:100]
        return f"Email action: {request}..."
    
    elif action == "schedule_event":
        request = args.get("request", "")[:100]
        return f"Calendar action: {request}..."
    
    else:
        # Generic description
        args_preview = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
        return f"{action}({args_preview})"


def format_interrupt_for_display(interrupt_data: HumanInterrupt) -> str:
    """Format an interrupt for console/log display.
    
    Args:
        interrupt_data: The interrupt to format
        
    Returns:
        Formatted string for display
    """
    action = interrupt_data["action_request"]["action"]
    args = interrupt_data["action_request"]["args"]
    config = interrupt_data["config"]
    description = interrupt_data.get("description", "")
    
    lines = [
        f"\n{'='*60}",
        f"🔔 ACTION REQUIRES APPROVAL",
        f"{'='*60}",
        f"Tool: {action}",
        f"Description: {description}",
        f"\nArguments:"
    ]
    
    for key, value in args.items():
        # Truncate long values
        str_value = str(value)
        if len(str_value) > 200:
            str_value = str_value[:200] + "..."
        lines.append(f"  • {key}: {str_value}")
    
    lines.append(f"\nAvailable actions:")
    if config["allow_accept"]:
        lines.append("  [A] Accept - Execute as shown")
    if config["allow_edit"]:
        lines.append("  [E] Edit - Modify arguments")
    if config["allow_respond"]:
        lines.append("  [R] Respond - Provide feedback")
    if config["allow_ignore"]:
        lines.append("  [I] Ignore - Skip this action")
    
    lines.append(f"{'='*60}\n")
    
    return "\n".join(lines)
