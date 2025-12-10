"""Agent Graph for Email Assistant with HITL Support.

This module creates the LangGraph agent with human-in-the-loop capabilities,
designed to work with the Agent Inbox UI.

Features:
- Uses Foundry Local singleton for persistent LLM connection
- Structured output for tool selection (Phi-4 compatible)
- Human-in-the-loop interrupts for sensitive actions
- Compatible with Agent Inbox schema

Usage:
    from email_agent.agent_graph import create_agent_graph
    
    graph = await create_agent_graph()
    result = await graph.ainvoke({"question": "..."}, config={"configurable": {"thread_id": "..."}})
"""

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from email_agent.email_storage import EmailStorage
from email_agent.foundry_service import get_foundry_llm
from email_agent.hitl_schemas import (
    HumanInterrupt,
    HumanResponse,
    create_interrupt,
    format_interrupt_for_display,
)
from email_agent.prompts import (
    MEMORY_UPDATE_INSTRUCTIONS,
    agent_system_prompt_hitl,
    default_background,
    default_cal_preferences,
    default_response_preferences,
)
from email_agent.schemas import RouterSchema, State, UserPreferences
from email_agent.tools.default.email_tools import Done, Question
from email_agent.tools.default.prompt_templates import AGENT_TOOLS_PROMPT
from email_agent.utils import parse_email

logger = logging.getLogger(__name__)

CURRENT_DATE = datetime.datetime.now(datetime.UTC).date().isoformat()


# Schema for tool selection
class ToolCallRequest(BaseModel):
    """Request to call a specific tool with arguments."""
    tool_name: str = Field(description="The name of the tool to call")
    tool_args: dict = Field(description="The arguments to pass to the tool as a dictionary")


# Tool categories for MCP
EMAIL_MCP_TOOLS = ["list-mail-messages", "create-draft-email", "get-mail-message", "send-mail"]
# Calendar tools
CALENDAR_MCP_TOOLS = [
    "get-calendar-view",     # Get events in date range (USE THIS for "this week", "today", etc.)
    "list-calendar-events",  # List events (use top param, for general listing)
    "get-calendar-event",    # Get specific event by ID
    "create-calendar-event", # Create new event
    "update-calendar-event", # Update existing event  
    "delete-calendar-event", # Delete event
]

# Tools that require human approval - ONLY write operations
# Note: manage_calendar is NOT here because listing meetings shouldn't require approval
# The underlying MCP tools (create-calendar-event, etc.) would require approval if called directly
HITL_TOOL_NAMES = {
    "send-mail",  # Sending emails requires approval
    "create-calendar-event",  # Creating events requires approval
    "create-specific-calendar-event",  # Creating events requires approval
    "manage_email",  # Email wrapper (includes sending)
    "Question"  # Agent asking user for clarification
}

# Global resources (lazy-loaded)
_email_storage: Optional[EmailStorage] = None
_mcp_tools: Optional[List] = None
_mcp_stdio_context = None
_mcp_session_context = None


def get_email_storage() -> EmailStorage:
    """Get the email storage singleton."""
    global _email_storage
    if _email_storage is None:
        _email_storage = EmailStorage()
    return _email_storage


async def load_mcp_tools():
    """Load MCP tools asynchronously."""
    global _mcp_tools, _mcp_stdio_context, _mcp_session_context
    
    if _mcp_tools is not None:
        return _mcp_tools
    
    logger.info("🔧 Loading MCP tools...")
    
    try:
        from langchain_mcp_adapters.tools import load_mcp_tools as load_mcp
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@softeria/ms-365-mcp-server", "--org-mode"],
            env=None
        )
        
        _mcp_stdio_context = stdio_client(server_params)
        stdio, write = await _mcp_stdio_context.__aenter__()
        _mcp_session_context = ClientSession(stdio, write)
        await _mcp_session_context.__aenter__()
        await _mcp_session_context.initialize()
        
        _mcp_tools = await load_mcp(_mcp_session_context)
        logger.info(f"✅ Loaded {len(_mcp_tools)} MCP tools")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to load MCP tools: {e}")
        _mcp_tools = []
    
    return _mcp_tools


# Memory helper functions
def get_memory(store, namespace: tuple, default_content: str = None) -> str:
    """Get memory from the store."""
    user_preferences = store.get(namespace, "user_preferences")
    if user_preferences:
        return user_preferences.value
    store.put(namespace, "user_preferences", default_content)
    return default_content


async def create_agent_graph():
    """Create the agent graph with HITL support.
    
    Returns:
        Compiled LangGraph StateGraph
    """
    # Get shared resources
    llm = get_foundry_llm()
    email_storage = get_email_storage()
    mcp_tools = await load_mcp_tools()
    
    # Separate MCP tools by category
    email_tools = [t for t in mcp_tools if t.name in EMAIL_MCP_TOOLS]
    calendar_tools = [t for t in mcp_tools if t.name in CALENDAR_MCP_TOOLS]
    
    # Build tool lookups for sub-agents
    calendar_tools_by_name = {t.name: t for t in calendar_tools}
    email_tools_by_name = {t.name: t for t in email_tools}
    
    # ========================================================================
    # Sub-Agent Architecture (Phi-4 compatible with structured output)
    # ========================================================================
    
    async def run_sub_agent(
        tools: list,
        tools_by_name: dict,
        system_prompt: str,
        user_request: str,
        max_iterations: int = 5
    ) -> str:
        """Run a sub-agent loop using structured output for tool selection.
        
        This replicates the behavior of create_agent but uses with_structured_output
        instead of bind_tools, making it compatible with Phi-4/Foundry Local.
        """
        # Build tool descriptions
        tool_descriptions = [f"- {t.name}: {t.description}" for t in tools]
        tools_text = "\n".join(tool_descriptions)
        
        # Create tool selector with structured output
        llm_tool_selector = llm.with_structured_output(ToolCallRequest, method='json_mode')
        
        # Track results
        tool_results = []
        last_raw_result = None  # Keep the raw result for fallback
        
        for iteration in range(max_iterations):
            # Build the prompt based on whether we have results
            if tool_results:
                results_text = "\n".join(tool_results)
                prompt = f"""{system_prompt}

USER REQUEST: {user_request}

TOOL RESULTS:
{results_text}

Based on the ACTUAL tool results above, provide an accurate summary.
CRITICAL: Only report what the tool ACTUALLY did. If you used create-draft-email, say "draft created" NOT "sent".

Respond with ONLY this JSON:
{{"tool_name": "DONE", "tool_args": {{"answer": "accurate summary of what was done"}}}}

JSON response:"""
            else:
                prompt = f"""{system_prompt}

Available tools:
{tools_text}

USER REQUEST: {user_request}

Select a tool. Respond with ONLY valid JSON:
{{"tool_name": "tool_name_here", "tool_args": {{"arg1": "value1"}}}}

JSON response:"""
            
            try:
                tool_request = llm_tool_selector.invoke(prompt)
                logger.info(f"🔧 Sub-agent selected: {tool_request.tool_name}({tool_request.tool_args})")
                
                # Check if agent is done
                if tool_request.tool_name == "DONE":
                    return tool_request.tool_args.get("answer", "Task completed.")
                
                # Execute the tool
                if tool_request.tool_name not in tools_by_name:
                    tool_results.append(f"Error: Tool '{tool_request.tool_name}' not found. Available: {list(tools_by_name.keys())}")
                    continue
                
                tool = tools_by_name[tool_request.tool_name]
                try:
                    result = await tool.ainvoke(tool_request.tool_args)
                    last_raw_result = str(result)
                    tool_results.append(f"Tool '{tool_request.tool_name}' returned: {last_raw_result[:1500]}")
                except Exception as e:
                    tool_results.append(f"Tool '{tool_request.tool_name}' error: {str(e)}")
                    
            except Exception as e:
                # If JSON parsing fails but we have tool results, return them directly
                if tool_results and last_raw_result:
                    logger.warning(f"Sub-agent JSON parse failed, returning raw results: {e}")
                    return last_raw_result[:2000]
                logger.error(f"Sub-agent error: {e}")
                return f"Error: {str(e)}"
        
        # Max iterations reached - return the raw result if we have it
        if last_raw_result:
            return last_raw_result[:2000]
        if tool_results:
            return "\n".join(tool_results)
        return "Could not complete the request."
    
    # Helper function to compute date ranges
    def get_date_range_for_query(query: str) -> tuple:
        """Compute start and end dates based on natural language query."""
        import datetime
        today = datetime.date.today()
        
        query_lower = query.lower()
        
        if "today" in query_lower:
            start = today
            end = today + datetime.timedelta(days=1)
        elif "tomorrow" in query_lower:
            start = today + datetime.timedelta(days=1)
            end = today + datetime.timedelta(days=2)
        elif "this week" in query_lower or "week" in query_lower:
            # Monday of current week to Sunday
            start = today - datetime.timedelta(days=today.weekday())
            end = start + datetime.timedelta(days=7)
        elif "next week" in query_lower:
            # Monday of next week to Sunday
            start = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)
            end = start + datetime.timedelta(days=7)
        elif "this month" in query_lower or "month" in query_lower:
            start = today.replace(day=1)
            # End of month
            if today.month == 12:
                end = today.replace(year=today.year + 1, month=1, day=1)
            else:
                end = today.replace(month=today.month + 1, day=1)
        else:
            # Default: next 7 days
            start = today
            end = today + datetime.timedelta(days=7)
        
        # Format as ISO 8601 with time
        start_str = f"{start.isoformat()}T00:00:00"
        end_str = f"{end.isoformat()}T23:59:59"
        return start_str, end_str
    
    # Create calendar sub-agent function
    async def calendar_sub_agent(request: str) -> str:
        """Calendar specialist sub-agent."""
        # Pre-compute date range if request seems time-based
        request_lower = request.lower()
        date_keywords = ["today", "tomorrow", "this week", "next week", "week", "this month", "month"]
        
        date_hint = ""
        if any(kw in request_lower for kw in date_keywords):
            start_dt, end_dt = get_date_range_for_query(request)
            date_hint = f"""

DATE RANGE HINT: For this query, use these dates:
- startDateTime: "{start_dt}"
- endDateTime: "{end_dt}"
"""
        
        return await run_sub_agent(
            tools=calendar_tools,
            tools_by_name=calendar_tools_by_name,
            system_prompt=f"""You are a calendar specialist. Current date: {CURRENT_DATE}

AVAILABLE TOOLS:
- get-calendar-view: Get events within a DATE RANGE. USE THIS for time-based queries ("this week", "today", "tomorrow", etc.)
  Args: {{"startDateTime": "YYYY-MM-DDTHH:MM:SS", "endDateTime": "YYYY-MM-DDTHH:MM:SS"}}
- list-calendar-events: List upcoming events without date filter. Use {{"top": 10}} for general listing.
- get-calendar-event: Get a specific event by ID. Use {{"id": "event_id"}}
- create-calendar-event: Create a new event
- update-calendar-event: Update an existing event
- delete-calendar-event: Delete an event

CRITICAL RULES:
1. For "this week", "today", "tomorrow", "this month" queries → MUST use "get-calendar-view" with startDateTime and endDateTime
2. For general "show my events" or "list meetings" → use "list-calendar-events"
3. Date format MUST be ISO 8601: YYYY-MM-DDTHH:MM:SS (e.g., "2025-12-03T00:00:00")
{date_hint}""",
            user_request=request
        )
    
    # Create email sub-agent function  
    async def email_sub_agent(request: str) -> str:
        """Email specialist sub-agent."""
        # Check if user explicitly wants to send (not draft)
        request_lower = request.lower()
        send_intent = "send" in request_lower and "draft" not in request_lower
        
        system_prompt = f"""You are an email specialist.

AVAILABLE TOOLS:
- send-mail: SEND an email immediately
- create-draft-email: Create a DRAFT only (saves to drafts folder)
- list-mail-messages: List emails from inbox
- get-mail-message: Get a specific email by ID

{"USER INTENT: The user wants to SEND the email (not just draft). Use send-mail." if send_intent else ""}

CRITICAL FORMAT for send-mail:
{{
  "body": {{
    "Message": {{
      "subject": "Your subject here",
      "body": {{ "content": "Your email body text here", "contentType": "text" }},
      "toRecipients": [{{ "emailAddress": {{ "address": "recipient@example.com" }} }}]
    }}
  }}
}}

CRITICAL FORMAT for create-draft-email:
{{
  "body": {{
    "subject": "Your subject here",
    "body": {{ "content": "Your email body text here", "contentType": "text" }},
    "toRecipients": [{{ "emailAddress": {{ "address": "recipient@example.com" }} }}]
  }}
}}

RULES:
1. The "body" field inside Message/email MUST be an object with "content" and "contentType", NOT a string!
2. Recipients must use the toRecipients array format shown above
3. Report EXACTLY what happened - if you drafted, say "draft created", if you sent, say "email sent"
"""
        return await run_sub_agent(
            tools=email_tools,
            tools_by_name=email_tools_by_name,
            system_prompt=system_prompt,
            user_request=request
        )
    
    # Define wrapper tools that call sub-agents
    @tool
    async def manage_calendar(request: str) -> str:
        """Manage calendar - list events, check availability, view meetings. Use for ANY calendar-related request."""
        if not calendar_tools:
            return "Calendar tools not available."
        return await calendar_sub_agent(request)
    
    @tool
    async def manage_email(request: str) -> str:
        """Manage emails - send, draft, list emails. Requires human approval for sending."""
        if not email_tools:
            return "Email tools not available."
        return await email_sub_agent(request)
    
    @tool
    async def search_email_history(query: str, top_k: int = 5) -> str:
        """Search email history for context. Does not require approval."""
        results = await email_storage.search(query, top_k)
        if not results:
            return "No emails found."
        return "\n\n".join([
            f"{i+1}. From: {r['author']}, Subject: {r['subject']}\n{r['snippet']}" 
            for i, r in enumerate(results)
        ])
    
    # Build supervisor tools list
    supervisor_tools = [search_email_history, Question, Done]
    if calendar_tools:
        supervisor_tools.insert(0, manage_calendar)
    if email_tools:
        supervisor_tools.insert(0, manage_email)
    
    tools_by_name = {t.name: t for t in supervisor_tools}
    
    # Create store
    store = InMemoryStore()
    
    # ========================================================================
    # Graph Nodes
    # ========================================================================
    
    async def supervisor(state: State) -> Dict[str, Any]:
        """Supervisor agent that coordinates sub-agents."""
        # Get user preferences from memory
        response_preferences = get_memory(store, ("email_assistant", "response_preferences"), default_response_preferences)
        cal_preferences = get_memory(store, ("email_assistant", "calendar_preferences"), default_cal_preferences)
        background = get_memory(store, ("email_assistant", "background"), default_background)
        
        # Build tool descriptions
        tool_descriptions = [f"- {t.name}: {t.description}" for t in supervisor_tools]
        tools_text = "\n".join(tool_descriptions)
        
        # Create LLM with structured output
        llm_tool_selector = llm.with_structured_output(ToolCallRequest, method='json_mode')
        
        # Determine user message
        if state.get("question"):
            user_message = state["question"]
            logger.info(f"💬 Supervisor processing question: {user_message}")
        elif state.get("email_input"):
            author, to, subject, email_thread = parse_email(state["email_input"])
            logger.info(f"📧 Supervisor processing email from {author}")
            user_message = f"Email from {author} about: {subject}. Content: {email_thread}"
        else:
            logger.warning("⚠️ No question or email_input in state")
            return {"messages": [AIMessage(content="No input provided.")]}
        
        # Check for existing messages (conversation history)
        messages = state.get("messages", [])
        
        # Build conversation history for context
        conversation_history = []
        tool_results_for_current_turn = []
        previous_tool_results = []
        
        # Track completed Q&A exchanges (question + Done answer pairs)
        current_question_in_messages = None
        
        for msg in messages:
            if isinstance(msg, HumanMessage):
                conversation_history.append(f"User: {msg.content}")
                current_question_in_messages = msg.content
            elif isinstance(msg, AIMessage):
                # Check if this is a Done response (final answer)
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tc in msg.tool_calls:
                        if tc.get("name") == "Done":
                            answer = tc.get("args", {}).get("answer", "")
                            conversation_history.append(f"Assistant: {answer}")
                            # Mark this Q&A as complete
                            current_question_in_messages = None
                elif msg.content:
                    conversation_history.append(f"Assistant: {msg.content}")
            elif isinstance(msg, ToolMessage):
                # Check if this tool result is for the CURRENT question being processed
                # or from a previous completed exchange
                if current_question_in_messages == user_message:
                    # This is a tool result for the current question (in same turn)
                    tool_results_for_current_turn.append(f"Tool '{msg.name}' returned: {msg.content[:500]}...")
                else:
                    # This is from a previous exchange - store for context
                    previous_tool_results.append(f"[Previous] Tool '{msg.name}': {msg.content[:300]}...")
        
        # Check if the current user_message is already in conversation history
        current_user_in_history = any(f"User: {user_message}" == h for h in conversation_history)
        
        # Build conversation context string
        if conversation_history:
            history_text = "\n".join(conversation_history[-10:])  # Last 10 exchanges
            if not current_user_in_history:
                context_section = f"""
PREVIOUS CONVERSATION:
{history_text}

CURRENT USER MESSAGE: {user_message}"""
            else:
                context_section = f"""
CONVERSATION SO FAR:
{history_text}"""
        else:
            context_section = f"USER REQUEST: {user_message}"
        
        # Build the prompt - KEY CHANGE: Let the LLM decide what to do
        if tool_results_for_current_turn:
            # We have fresh tool results for the CURRENT question - synthesize answer
            results_text = "\n".join(tool_results_for_current_turn)
            tool_selection_prompt = f"""You are a tool-calling assistant. Your response must be ONLY valid JSON, nothing else.

TODAY'S DATE: {CURRENT_DATE}

{context_section}

TOOL RESULTS FOR CURRENT REQUEST:
{results_text}

Based on the tool results above, provide a final answer using the Done tool.

Available tools:
{tools_text}

Respond with ONLY this JSON format (no other text):
{{"tool_name": "Done", "tool_args": {{"answer": "your synthesized answer based on the tool results"}}}}

JSON response:"""
        else:
            # No tool results yet for current question - decide what to do
            # Include previous context so LLM can decide if it needs new tools or can answer from history
            previous_context = ""
            if previous_tool_results:
                previous_context = f"""
PREVIOUS TOOL RESULTS (from earlier in conversation):
{chr(10).join(previous_tool_results[-5:])}
"""
            
            tool_selection_prompt = f"""You are a tool-calling assistant. Your response must be ONLY valid JSON, nothing else.

TODAY'S DATE: {CURRENT_DATE}

Available tools:
- manage_calendar: Manage calendar - list events, check availability, create/update/delete/reschedule events. REQUIRED arg: "request" (string describing what to do)
  USE THIS FOR: meetings, appointments, events, schedules, calendar queries, rescheduling
- manage_email: Manage emails - send, draft, list, or search emails. REQUIRED arg: "request" (string describing what to do)
  USE THIS FOR: sending emails, drafting emails, listing inbox messages
- search_email_history: Search past emails for context. Args: "query" (search terms), "top_k" (number of results, default 5)
  USE THIS FOR: finding old emails, searching email history for information
- Question: Ask user for clarification. REQUIRED arg: "question" (the question to ask)
- Done: Provide final answer. REQUIRED arg: "answer" (the answer text)

IMPORTANT TOOL SELECTION RULES:
- "meeting" or "reschedule meeting" → ALWAYS use manage_calendar (meetings ARE calendar events)
- "send email" → use manage_email
- "find email" or "search emails" → use search_email_history

{context_section}
{previous_context}

INSTRUCTIONS:
1. If the user's CURRENT question can be answered using information from the PREVIOUS CONVERSATION above, use the Done tool to answer directly.
2. If the user's question requires NEW information (different topic, new search, etc.), select the appropriate tool.
3. For calendar/meeting questions (including rescheduling) → use manage_calendar with a clear request
4. For sending or drafting emails → use manage_email
5. For searching old emails → use search_email_history
6. For clarification needed → use Question

CRITICAL: A "meeting" is a CALENDAR event, NOT an email. To reschedule a meeting, use manage_calendar.

IMPORTANT: Every tool call MUST include the required arguments. Never call a tool with empty args {{}}. Do NOT claim a draft was created unless a tool actually returned confirmation.

Respond with ONLY this JSON format (no other text):
{{"tool_name": "name_of_tool", "tool_args": {{"required_arg": "value"}}}}

Examples:
- Reschedule meeting: {{"tool_name": "manage_calendar", "tool_args": {{"request": "find meeting on November 4th and reschedule it"}}}}
- List meetings: {{"tool_name": "manage_calendar", "tool_args": {{"request": "list my meetings this week"}}}}
- Send email: {{"tool_name": "manage_email", "tool_args": {{"request": "send email to user@example.com saying hello"}}}}
- Search old emails: {{"tool_name": "search_email_history", "tool_args": {{"query": "meeting November", "top_k": 5}}}}
- Final answer: {{"tool_name": "Done", "tool_args": {{"answer": "Based on the results..."}}}}
- Ask user: {{"tool_name": "Question", "tool_args": {{"question": "Could you clarify..."}}}}

JSON response:"""
        
        try:
            tool_request = llm_tool_selector.invoke(tool_selection_prompt)
            logger.info(f"🔧 Tool selected: {tool_request.tool_name}({tool_request.tool_args})")
            
            # Validate and fix common issues with tool args
            tool_args = tool_request.tool_args or {}
            
            # Fix empty args for tools that require them
            if tool_request.tool_name == "manage_calendar" and not tool_args.get("request"):
                # Infer the request from the user message
                tool_args["request"] = user_message
                logger.info(f"🔧 Fixed empty manage_calendar args: {tool_args}")
            elif tool_request.tool_name == "manage_email" and not tool_args.get("request"):
                tool_args["request"] = user_message
                logger.info(f"🔧 Fixed empty manage_email args: {tool_args}")
            elif tool_request.tool_name == "search_email_history" and not tool_args.get("query"):
                tool_args["query"] = user_message
                tool_args["top_k"] = tool_args.get("top_k", 5)
                logger.info(f"🔧 Fixed empty search_email_history args: {tool_args}")
            
            # Create AIMessage with tool_calls
            ai_message = AIMessage(
                content="",
                tool_calls=[{
                    "name": tool_request.tool_name,
                    "args": tool_args,
                    "id": f"call_{datetime.datetime.now().timestamp()}",
                    "type": "tool_call"
                }]
            )
            
            # Determine if we need to add a HumanMessage
            # Add HumanMessage if:
            # 1. No messages exist (first call), OR
            # 2. This is a new user question (follow-up) that isn't in message history yet
            need_human_message = not messages or not current_user_in_history
            
            if need_human_message:
                return {"messages": [HumanMessage(content=user_message), ai_message]}
            else:
                return {"messages": [ai_message]}
            
        except Exception as e:
            logger.warning(f"Tool selection failed: {e}")
            need_human_message = not messages or not current_user_in_history
            if need_human_message:
                return {"messages": [HumanMessage(content=user_message), AIMessage(content=f"Error: {e}")]}
            else:
                return {"messages": [AIMessage(content=f"Error: {e}")]}
    
    def hitl_gate(state: State) -> Dict[str, Any]:
        """Check if HITL approval is needed and interrupt if so."""
        messages = state.get("messages", [])
        if not messages:
            return state
        
        last_message = messages[-1]
        
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return state
        
        for tool_call in last_message.tool_calls:
            tool_name = tool_call.get("name")
            
            # Check if this tool requires HITL
            if tool_name in HITL_TOOL_NAMES:
                tool_args = tool_call.get("args", {})
                
                # Create interrupt in Agent Inbox format
                interrupt_data = create_interrupt(
                    action=tool_name,
                    args=tool_args,
                    description=None  # Auto-generated
                )
                
                logger.info(f"🔔 HITL required for {tool_name}")
                logger.info(format_interrupt_for_display(interrupt_data))
                
                # Interrupt and wait for human response
                response = interrupt(interrupt_data)
                
                # Handle the response
                if isinstance(response, dict):
                    response_type = response.get("type", "accept")
                    response_args = response.get("args")
                    
                    if response_type == "ignore":
                        logger.info(f"⏭️ User ignored {tool_name}")
                        # Remove the tool call and continue
                        return {"messages": messages[:-1]}
                    
                    elif response_type == "edit" and response_args:
                        logger.info(f"✏️ User edited {tool_name} args")
                        # Update tool call with new args
                        new_args = response_args.get("args", tool_args)
                        last_message.tool_calls[0]["args"] = new_args
                    
                    elif response_type == "response":
                        logger.info(f"💬 User responded to {tool_name}")
                        # Add user response as a message
                        user_response = HumanMessage(content=str(response_args))
                        return {"messages": messages + [user_response]}
                    
                    # "accept" - continue as-is
                    logger.info(f"✅ User accepted {tool_name}")
        
        return state
    
    def should_continue(state: State) -> str:
        """Determine if agent should continue or end."""
        messages = state.get("messages", [])
        if not messages:
            return END
        
        last_message = messages[-1]
        
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            for tool_call in last_message.tool_calls:
                tool_name = tool_call.get("name")
                if tool_name in ("Done", "Question"):
                    args = tool_call.get("args", {})
                    if tool_name == "Done":
                        logger.info(f"✅ DONE - Final answer: {args.get('answer', 'No answer provided')}")
                    else:
                        logger.info(f"❓ QUESTION - Asking user: {args.get('question', 'No question provided')}")
                    return END
            
            return "hitl_gate"
        
        return END
    
    def after_hitl(state: State) -> str:
        """Route after HITL gate."""
        messages = state.get("messages", [])
        if not messages:
            return END
        
        last_message = messages[-1]
        
        # If last message has tool calls, execute them
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        
        # If user provided a response, go back to supervisor
        if isinstance(last_message, HumanMessage):
            return "supervisor"
        
        return END
    
    # ========================================================================
    # Build Graph
    # ========================================================================
    
    workflow = StateGraph(State)
    
    # Add nodes
    workflow.add_node("supervisor", supervisor)
    workflow.add_node("hitl_gate", hitl_gate)
    workflow.add_node("tools", ToolNode(supervisor_tools))
    
    # Add edges
    workflow.add_edge(START, "supervisor")
    workflow.add_conditional_edges("supervisor", should_continue, ["hitl_gate", END])
    workflow.add_conditional_edges("hitl_gate", after_hitl, ["tools", "supervisor", END])
    workflow.add_edge("tools", "supervisor")
    
    # Compile with checkpointer
    checkpointer = MemorySaver()
    graph = workflow.compile(checkpointer=checkpointer, store=store)
    
    logger.info("✅ Agent graph created with HITL support")
    
    return graph


async def cleanup_mcp():
    """Cleanup MCP resources."""
    global _mcp_session_context, _mcp_stdio_context
    
    if _mcp_session_context:
        await _mcp_session_context.__aexit__(None, None, None)
        _mcp_session_context = None
    
    if _mcp_stdio_context:
        await _mcp_stdio_context.__aexit__(None, None, None)
        _mcp_stdio_context = None
