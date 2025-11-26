"""msft Email Assistant with Foundry Local (Phi-4) - Local model version.

This is a clone of msft_email_agent.py that uses Foundry Local with Phi-4-generic-gpu
instead of Azure OpenAI gpt-5-mini.

Changes from original:
- Uses Foundry Local with Phi-4-generic-gpu model
- Temperature set to 0.0 for maximum accuracy
- All LLM calls use local model
- Custom middleware for Phi-4 tool calling (JSON-to-tool-call conversion)
- Tool result trimming to prevent context overflow

Quick Start:
    1. Ensure Foundry Local service is running
    2. Import past emails: python -m email_agent.import_emails --months 1
    3. Run agent: python -m email_agent.msft_email_agent_local
"""

import asyncio
import datetime
import logging
import os
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from foundry_local import FoundryLocalManager
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from email_agent.email_storage import EmailStorage
from email_agent.prompts import (
    MEMORY_UPDATE_INSTRUCTIONS,
    MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT,
    agent_system_prompt_hitl,
    default_background,
    default_cal_preferences,
    default_response_preferences,
    default_triage_instructions,
    triage_system_prompt,
    triage_user_prompt,
)
from email_agent.schemas import RouterSchema, State, UserPreferences
from email_agent.tools.default.email_tools import Done, Question
from email_agent.tools.default.prompt_templates import AGENT_TOOLS_PROMPT
from email_agent.utils import (
    format_email_markdown,
    format_for_display,
    parse_email,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CURRENT_DATE = datetime.datetime.now(datetime.UTC).date().isoformat()

# Custom middleware to convert Phi's JSON output to tool calls
class PhiJSONToToolCallMiddleware(AgentMiddleware):
    """Middleware that helps Phi generate proper tool calls by leveraging its JSON generation capability.
    
    Instead of trying to teach Phi the tool_calls format, we:
    1. Let Phi generate JSON describing what tool to call
    2. Parse that JSON and convert it to proper tool_calls format
    3. Replace the model's response with the structured tool call
    """
    
    def __init__(self, tools):
        self.tools = tools
        self.tool_schemas = self._build_tool_schemas(tools)
    
    def _build_tool_schemas(self, tools):
        """Build JSON schemas for all available tools."""
        schemas = {}
        for tool in tools:
            # Handle both Pydantic models and dict schemas
            if hasattr(tool, 'args_schema') and tool.args_schema:
                if hasattr(tool.args_schema, 'model_json_schema'):
                    # Pydantic v2
                    parameters = tool.args_schema.model_json_schema()
                elif hasattr(tool.args_schema, 'schema'):
                    # Pydantic v1
                    parameters = tool.args_schema.schema()
                elif isinstance(tool.args_schema, dict):
                    # Already a dict schema
                    parameters = tool.args_schema
                else:
                    parameters = {}
            else:
                parameters = {}
            
            schemas[tool.name] = {
                "name": tool.name,
                "description": tool.description,
                "parameters": parameters
            }
        return schemas
    
    def after_model(self, state, runtime):
        """Parse model's response and convert JSON to tool calls if needed."""
        messages = state.get("messages", [])
        if not messages:
            return None
        
        last_message = messages[-1]
        if not isinstance(last_message, AIMessage):
            return None
        
        content = last_message.content
        if not content or not isinstance(content, str):
            return None
        
        # Check if already has tool_calls (don't process twice)
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return None
        
        # Look for JSON pattern: {"name": "tool_name", "args": {...}}
        import json
        import re
        
        # More flexible regex to find the JSON object
        json_match = re.search(r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"args"\s*:\s*(\{[^}]*\})\s*\}', content, re.DOTALL)
        if json_match:
            tool_name = json_match.group(1)
            try:
                args_str = json_match.group(2)
                tool_args = json.loads(args_str)
                
                if tool_name in self.tool_schemas:
                    # Create proper tool call
                    new_message = AIMessage(
                        content="",
                        tool_calls=[{
                            "name": tool_name,
                            "args": tool_args,
                            "id": f"call_{len(messages)}",
                            "type": "tool_call"
                        }]
                    )
                    # Replace last message
                    new_messages = messages[:-1] + [new_message]
                    logger.info(f"✅ Converted JSON to tool call: {tool_name}({tool_args})")
                    return {"messages": new_messages}
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse tool args: {e}")
        
        return None

# Middleware to help Phi synthesize answers after tool calls
class PhiToolResultMiddleware(AgentMiddleware):
    """Middleware that helps Phi understand it should answer after tool results.
    
    After a tool returns results, Phi sometimes returns empty content.
    This middleware:
    1. Trims tool results to prevent context overflow
    2. Always adds a reminder to synthesize an answer
    """
    
    def before_model(self, state, runtime):
        """Trim tool results and add answer reminder."""
        messages = state.get("messages", [])
        if not messages:
            return None
        
        # Check if last message is a ToolMessage
        last_msg = messages[-1]
        if not isinstance(last_msg, ToolMessage):
            return None
        
        # Trim tool result if too long
        content = last_msg.content
        tool_msg = last_msg
        
        if isinstance(content, str) and len(content) > 1000:
            # Keep first 1000 chars
            trimmed_content = content[:1000] + f"\n... (trimmed {len(content) - 1000} chars)"
            tool_msg = ToolMessage(
                content=trimmed_content,
                name=last_msg.name,
                tool_call_id=last_msg.tool_call_id
            )
            logger.info(f"✂️ Trimmed {last_msg.name} result from {len(content)} to 1000 chars")
        
        # Always add a reminder to synthesize an answer
        reminder = HumanMessage(
            content="Now synthesize a complete answer to the user's question based on the tool results above. Be specific and include relevant details."
        )
        
        new_messages = messages[:-1] + [tool_msg, reminder]
        logger.info("💡 Added synthesis reminder after tool result")
        return {"messages": new_messages}

# Initialize Foundry Local LLM
logger.info("🚀 Initializing Foundry Local with Phi-4-generic-gpu...")
foundry_manager = FoundryLocalManager("Phi-4-generic-gpu")
logger.info(f"✅ Foundry endpoint: {foundry_manager.endpoint}")

llm = ChatOpenAI(
    base_url=foundry_manager.endpoint,
    api_key=foundry_manager.api_key,
    model="Phi-4-generic-gpu",
    temperature=0.0  # Maximum accuracy for email processing
)

llm_router = llm.with_structured_output(RouterSchema)

# Tool categories
EMAIL_MCP_TOOLS = ["list-mail-messages", "create-draft-email", "get-mail-message", "send-mail"]
CALENDAR_MCP_TOOLS = [
    "list-calendars", "list-specific-calendar-events", "create-specific-calendar-event",
    "get-specific-calendar-event", "update-specific-calendar-event", "delete-specific-calendar-event",
    "get-calendar-view", "list-calendar-events", "create-calendar-event",
    "get-calendar-event", "update-calendar-event", "delete-calendar-event",
]
AUTHENTICATION_MCP_TOOLS = ["login", "verify-login", "get-current-user"]

# Global MCP tools
_mcp_tools = None
_mcp_stdio_context = None
_mcp_session_context = None


# Initialize storage
email_storage = EmailStorage()  # Reads STORAGE_TYPE from env


# Memory helper functions
def get_memory(store: BaseStore, namespace: tuple, default_content: str = None) -> str:
    """Get memory from the store or initialize with default if it doesn't exist.
    
    Args:
        store: LangGraph BaseStore instance to search for existing memory
        namespace: Tuple defining the memory namespace, e.g. ("email_assistant", "triage_preferences")
        default_content: Default content to use if memory doesn't exist
        
    Returns:
        str: The content of the memory profile, either from existing memory or the default
    """
    # Search for existing memory with namespace and key
    user_preferences = store.get(namespace, "user_preferences")
    
    # If memory exists, return its content (the value)
    if user_preferences:
        return user_preferences.value
    
    # If memory doesn't exist, add it to the store and return the default content
    else:
        # Namespace, key, value
        store.put(namespace, "user_preferences", default_content)
        user_preferences = default_content
    
    # Return the content
    return user_preferences


def update_memory(store: BaseStore, namespace: tuple, messages: list):
    """Update memory profile in the store.
    
    Args:
        store: LangGraph BaseStore instance to update memory
        namespace: Tuple defining the memory namespace, e.g. ("email_assistant", "triage_preferences")
        messages: List of messages to update the memory with
    """
    # Get the existing memory
    user_preferences = store.get(namespace, "user_preferences")
    
    # Update the memory using LLM with structured output
    memory_llm = ChatOpenAI(
        base_url=foundry_manager.endpoint,
        api_key=foundry_manager.api_key,
        model="Phi-4-generic-gpu",
        temperature=0.0
    ).with_structured_output(UserPreferences)
    
    result = memory_llm.invoke([
        {"role": "system", "content": MEMORY_UPDATE_INSTRUCTIONS.format(
            current_profile=user_preferences.value if user_preferences else "",
            namespace=namespace
        )},
    ] + messages)
    
    # Save the updated memory to the store
    store.put(namespace, "user_preferences", result.user_preferences)
    logger.info(f"✓ Updated memory for {namespace}")


async def _load_mcp_tools_async():
    """Load MCP tools with proper context management."""
    global _mcp_tools, _mcp_stdio_context, _mcp_session_context
    
    if _mcp_tools is not None:
        return _mcp_tools
    
    logger.info("🔧 Loading MCP tools...")
    
    server_params = StdioServerParameters(
        command="npx",
        args=[
            "-y",
            "@softeria/ms-365-mcp-server",
            "--org-mode"
        ],
        env=None
    )
    
    _mcp_stdio_context = stdio_client(server_params)
    stdio, write = await _mcp_stdio_context.__aenter__()
    _mcp_session_context = ClientSession(stdio, write)
    await _mcp_session_context.__aenter__()
    await _mcp_session_context.initialize()
    
    _mcp_tools = await load_mcp_tools(_mcp_session_context)
    logger.info(f"✅ Loaded {len(_mcp_tools)} MCP tools")
    
    return _mcp_tools


async def _initialize_agents_and_graph_async():
    """Initialize agents and graph asynchronously."""
    mcp_tools = await _load_mcp_tools_async()
    
    # Separate tools by category
    email_tools = []
    calendar_tools = []
    auth_tools = []
    
    for tool in mcp_tools:
        if tool.name in EMAIL_MCP_TOOLS:
            email_tools.append(tool)
        elif tool.name in CALENDAR_MCP_TOOLS:
            calendar_tools.append(tool)
        elif tool.name in AUTHENTICATION_MCP_TOOLS:
            auth_tools.append(tool)
    
    # Create sub-agents with JSON-to-tool-call middleware and result trimming
    calendar_agent = create_agent(
        model=llm,
        tools=calendar_tools,
        system_prompt="You are a calendar specialist. When you need to use a tool, respond with JSON: {\"name\": \"tool_name\", \"args\": {...}}. Use list-calendar-events with ONLY {\"top\": 10}.",
        middleware=[
            PhiJSONToToolCallMiddleware(calendar_tools),
            PhiToolResultMiddleware()
        ]
    )
    
    email_agent = create_agent(
        model=llm, 
        tools=email_tools, 
        system_prompt="You are an email specialist. When you need to use a tool, respond with JSON: {\"name\": \"tool_name\", \"args\": {...}}.",
        middleware=[
            PhiJSONToToolCallMiddleware(email_tools),
            PhiToolResultMiddleware()
        ]
    )
    
    # High-level tools
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import tool
    
    @tool
    async def schedule_event(request: str) -> str:
        """Schedule calendar events."""
        messages = [HumanMessage(content=f"{request}\n\nCurrent date: {CURRENT_DATE}")]
        result = await calendar_agent.ainvoke({"messages": messages})
        return result["messages"][-1].content
    
    @tool
    async def manage_email(request: str) -> str:
        """Send emails."""
        messages = [HumanMessage(content=request)]
        result = await email_agent.ainvoke({"messages": messages})
        return result["messages"][-1].content
    
    @tool
    async def search_email_history(query: str, top_k: int = 5) -> str:
        """Search email history for context."""
        results = await email_storage.search(query, top_k)
        if not results:
            return "No emails found."
        return "\n\n".join([f"{i+1}. From: {r['author']}, Subject: {r['subject']}\n{r['snippet']}" for i, r in enumerate(results)])
    
    supervisor_tools = [schedule_event, manage_email, search_email_history, Question, Done]
    
    # Create the supervisor node
    async def supervisor(state: State, supervisor_tools: list, tools_by_name: dict, store: BaseStore):
        """Supervisor agent that coordinates sub-agents."""
        # Get user preferences from memory
        response_preferences = get_memory(store, ("email_assistant", "response_preferences"), default_response_preferences)
        cal_preferences = get_memory(store, ("email_assistant", "calendar_preferences"), default_cal_preferences)
        default_background = get_memory(store, ("email_assistant", "background"), "")
        
        # Enhanced system prompt that handles both scenarios - using HITL prompt optimized for Phi-4
        enhanced_system_prompt = f"""{agent_system_prompt_hitl.format(
            tools_prompt=AGENT_TOOLS_PROMPT,
            background=default_background,
            response_preferences=response_preferences,
            cal_preferences=cal_preferences
        )}
        Additional capabilities:
        - When asked questions about email history, use the search_email_history tool
        - Synthesize information from multiple emails when needed
        - Provide specific details (dates, senders, subjects) when answering questions
        - Use the Question tool to ask the user for clarification when needed
        - Use the Done tool when you have completed all necessary actions
        - Current date: {CURRENT_DATE}
        
        TOOL CALLING: When you need to use a tool, respond with JSON: {{"name": "tool_name", "args": {{...}}}}
        """
        
        supervisor_agent = create_agent(
            model=llm,
            tools=supervisor_tools,
            system_prompt=enhanced_system_prompt,
            middleware=[
                PhiJSONToToolCallMiddleware(supervisor_tools),
                PhiToolResultMiddleware()
            ]
        )
        
        # Determine if this is a question or an email to respond to
        if state.get("question"):
            # Handle question about emails
            question = state["question"]
            logger.info(f"💬 Supervisor processing question: {question}")
            user_message = question
        else:
            # Handle email response
            author, to, subject, email_thread = parse_email(state["email_input"])
            logger.info(f"📧 Supervisor processing email from {author}")
            user_message = f"Email from {author} about: {subject}. Content: {email_thread}"
        
        messages = [HumanMessage(content=user_message)]
        
        result = await supervisor_agent.ainvoke({"messages": messages})
        
        return {"messages": result.get("messages", [])}


    def interrupt_handler(state: State, tools_by_name: dict, hitl_tool_names: set, store: BaseStore):
        """Handle interrupts for HITL tools only (send-mail, create-calendar-event, Question)."""
        result = []
        
        for msg in state.get("messages", []):
            if not hasattr(msg, "tool_calls") or not msg.tool_calls:
                continue
                
            for tool_call in msg.tool_calls:
                tool_name = tool_call.get("name")
                
                # Only interrupt for HITL tools
                if tool_name not in hitl_tool_names:
                    continue
                
                # Format tool call for display
                tool_args = tool_call.get("args", {})
                formatted_call = f"\n🔧 Tool: {tool_name}\n"
                for key, value in tool_args.items():
                    formatted_call += f"  • {key}: {value}\n"
                
                result.append(interrupt(value=formatted_call))
        
        return result if result else None


    def should_continue(state: State):
        """Determine if agent should continue or end."""
        messages = state.get("messages", [])
        if not messages:
            return END
        
        last_message = messages[-1]
        
        # Check if last message has tool calls
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.info(f"🔄 Found {len(last_message.tool_calls)} tool calls - continuing")
            return "tools"
        
        logger.info("✅ No tool calls - ending")
        return END


    # Build graph
    tools_by_name = {tool.name: tool for tool in supervisor_tools}
    hitl_tool_names = {"send-mail", "create-calendar-event", "create-specific-calendar-event", "Question"}
    
    # Create store first
    store = InMemoryStore()
    
    # Create a wrapper that can be called from sync context
    async def supervisor_wrapper(state):
        return await supervisor(state, supervisor_tools, tools_by_name, store)
    
    workflow = StateGraph(State)
    workflow.add_node("supervisor", supervisor_wrapper)
    workflow.add_node("tools", ToolNode(supervisor_tools))
    workflow.add_edge(START, "supervisor")
    workflow.add_conditional_edges("supervisor", should_continue, ["tools", END])
    workflow.add_edge("tools", "supervisor")
    
    # Compile with checkpointer and store
    checkpointer = MemorySaver()
    graph = workflow.compile(checkpointer=checkpointer, store=store, interrupt_before=["tools"])
    
    return graph


async def test():
    """Test the email agent with a sample question."""
    graph = await _initialize_agents_and_graph_async()
    
    logger.info("\n" + "="*60)
    logger.info("Test: Asking a question about emails")
    logger.info("="*60)
    
    # Test with a question
    logger.info("❓ QUESTION - Routing to supervisor")
    result = await graph.ainvoke(
        {
            "question": "Check my emails, and based on the latest one, find what time is my meeting on November 4th?",
            "classification_decision": None
        },
        config={"configurable": {"thread_id": "test-1"}}
    )
    
    logger.info(f"Result: {result}")


if __name__ == "__main__":
    async def main():
        """Main function to run the test and cleanup properly."""
        try:
            await test()
        finally:
            # Cleanup MCP resources
            if _mcp_session_context:
                await _mcp_session_context.__aexit__(None, None, None)
            if _mcp_stdio_context:
                await _mcp_stdio_context.__aexit__(None, None, None)
    
    asyncio.run(main())
