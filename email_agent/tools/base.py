from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import BaseTool


def get_tools(tool_names: Optional[List[str]] = None, include_gmail: bool = False) -> List[BaseTool]:
    """Get specified tools or all tools if tool_names is None.
    
    Args:
        tool_names: Optional list of tool names to include. If None, returns all tools.
        include_gmail: Whether to include Gmail tools. Defaults to False.
        
    Returns:
        List of tool objects
    """
    # Import default tools
    from email_agent.tools.default.calendar_tools import (
        check_calendar_availability,
        schedule_meeting,
    )
    from email_agent.tools.default.email_tools import Done, Question, write_email
    
    # Base tools dictionary
    all_tools = {
        "write_email": write_email,
        "Done": Done,
        "Question": Question,
        "schedule_meeting": schedule_meeting,
        "check_calendar_availability": check_calendar_availability,
    }
    
    # Add Gmail tools if requested
    if include_gmail:
        try:
            from email_agent.tools.gmail.gmail_tools import (
                check_calendar_tool,
                fetch_emails_tool,
                schedule_meeting_tool,
                send_email_tool,
            )
            
            all_tools.update({
                "fetch_emails_tool": fetch_emails_tool,
                "send_email_tool": send_email_tool,
                "check_calendar_tool": check_calendar_tool,
                "schedule_meeting_tool": schedule_meeting_tool,
            })
        except ImportError:
            # If Gmail tools aren't available, continue without them
            pass
    
    if tool_names is None:
        return list(all_tools.values())
    
    return [all_tools[name] for name in tool_names if name in all_tools]

def get_tools_by_name(tools: Optional[List[BaseTool]] = None) -> Dict[str, BaseTool]:
    """Get a dictionary of tools mapped by name."""
    if tools is None:
        tools = get_tools()
    
    return {tool.name: tool for tool in tools}
