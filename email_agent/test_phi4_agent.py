"""Test Phi-4 Agent with Fake Data - No PostgreSQL or MCP Required

This is a simplified test agent to verify Phi-4 function calling works correctly.
Uses fake email/calendar data stored in JSON files.

Quick Start:
    1. Ensure Foundry Local service is running
    2. Run: python -m email_agent.test_phi4_agent
"""

import asyncio
import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from foundry_local import FoundryLocalManager
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CURRENT_DATE = datetime.datetime.now(datetime.UTC).date().isoformat()

# State definition
class TestState(TypedDict):
    messages: List[Any]
    question: str


# Middleware to help Phi synthesize answers after tool calls
class PhiToolResultMiddleware(AgentMiddleware):
    """Middleware that helps Phi understand it should answer after tool results.
    
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


# Fake data storage location
FAKE_DATA_DIR = Path(__file__).parent / "test_data"

def load_fake_data():
    """Load fake data from JSON files in test_data folder."""
    emails_file = FAKE_DATA_DIR / "emails.json"
    calendar_file = FAKE_DATA_DIR / "calendar.json"
    
    if not emails_file.exists() or not calendar_file.exists():
        logger.error(f"❌ Fake data files not found in {FAKE_DATA_DIR}")
        logger.error(f"   Expected: emails.json and calendar.json")
        logger.error(f"   Please ensure test data files exist before running.")
        raise FileNotFoundError(f"Missing test data files in {FAKE_DATA_DIR}")
    
    logger.info(f"✓ Loading fake data from {FAKE_DATA_DIR}")


# Tool definitions
@tool
def search_emails(query: str, max_results: int = 5) -> str:
    """Search emails for a specific query string.
    
    Args:
        query: Search query to find in emails
        max_results: Maximum number of results to return (default: 5)
    
    Returns:
        Formatted string with matching emails
    """
    logger.info(f"🔍 Searching emails for: {query}")
    
    emails_file = FAKE_DATA_DIR / "emails.json"
    with open(emails_file, 'r') as f:
        emails = json.load(f)
    
    # Simple search - check if query appears in subject or body
    results = []
    query_lower = query.lower()
    
    for email in emails:
        if (query_lower in email['subject'].lower() or 
            query_lower in email['body'].lower() or
            query_lower in email['from'].lower()):
            results.append(email)
    
    # Sort by date (most recent first)
    results.sort(key=lambda x: x['date'], reverse=True)
    results = results[:max_results]
    
    if not results:
        return "No emails found matching your query."
    
    # Format results
    formatted = []
    for i, email in enumerate(results, 1):
        formatted.append(
            f"{i}. From: {email['from']}\n"
            f"   Subject: {email['subject']}\n"
            f"   Date: {email['date']}\n"
            f"   Body: {email['body'][:200]}{'...' if len(email['body']) > 200 else ''}"
        )
    
    return "\n\n".join(formatted)


@tool
def get_latest_emails(count: int = 5) -> str:
    """Get the most recent emails.
    
    Args:
        count: Number of recent emails to retrieve (default: 5)
    
    Returns:
        Formatted string with recent emails
    """
    logger.info(f"📧 Getting {count} latest emails")
    
    emails_file = FAKE_DATA_DIR / "emails.json"
    with open(emails_file, 'r') as f:
        emails = json.load(f)
    
    # Sort by date (most recent first)
    emails.sort(key=lambda x: x['date'], reverse=True)
    recent = emails[:count]
    
    # Format results
    formatted = []
    for i, email in enumerate(recent, 1):
        formatted.append(
            f"{i}. From: {email['from']}\n"
            f"   Subject: {email['subject']}\n"
            f"   Date: {email['date']}\n"
            f"   Body: {email['body']}"
        )
    
    return "\n\n".join(formatted)


@tool
def search_calendar(query: str = None, date: str = None) -> str:
    """Search calendar events by query or date.
    
    Args:
        query: Optional search query for event subject
        date: Optional date in YYYY-MM-DD format to filter events
    
    Returns:
        Formatted string with matching calendar events
    """
    logger.info(f"📅 Searching calendar - query: {query}, date: {date}")
    
    calendar_file = FAKE_DATA_DIR / "calendar.json"
    with open(calendar_file, 'r') as f:
        events = json.load(f)
    
    # Filter by query if provided
    if query:
        query_lower = query.lower()
        events = [e for e in events if query_lower in e['subject'].lower()]
    
    # Filter by date if provided
    if date:
        events = [e for e in events if e['start'].startswith(date)]
    
    if not events:
        return "No calendar events found."
    
    # Format results
    formatted = []
    for i, event in enumerate(events, 1):
        start_time = event['start']
        end_time = event['end']
        location = event.get('location', 'Not specified')
        attendees = ', '.join(event.get('attendees', []))
        
        formatted.append(
            f"{i}. {event['subject']}\n"
            f"   Start: {start_time}\n"
            f"   End: {end_time}\n"
            f"   Location: {location}\n"
            f"   Attendees: {attendees}"
        )
    
    return "\n\n".join(formatted)


@tool
def get_upcoming_events(days: int = 7) -> str:
    """Get upcoming calendar events for the next N days.
    
    Args:
        days: Number of days to look ahead (default: 7)
    
    Returns:
        Formatted string with upcoming events
    """
    logger.info(f"📆 Getting events for next {days} days")
    
    calendar_file = FAKE_DATA_DIR / "calendar.json"
    with open(calendar_file, 'r') as f:
        events = json.load(f)
    
    # Sort by start time
    events.sort(key=lambda x: x['start'])
    
    # Format results
    formatted = []
    for i, event in enumerate(events, 1):
        start_time = event['start']
        end_time = event['end']
        location = event.get('location', 'Not specified')
        
        formatted.append(
            f"{i}. {event['subject']}\n"
            f"   When: {start_time} to {end_time}\n"
            f"   Where: {location}"
        )
    
    return "\n\n".join(formatted) if formatted else "No upcoming events."


# Initialize Foundry Local LLM
logger.info("🚀 Initializing Foundry Local with Phi-4-generic-gpu...")
foundry_manager = FoundryLocalManager("Phi-4-generic-gpu")
logger.info(f"✅ Foundry endpoint: {foundry_manager.endpoint}")

llm = ChatOpenAI(
    base_url=foundry_manager.endpoint,
    api_key=foundry_manager.api_key,
    model="Phi-4-generic-gpu",
    temperature=0.0
)


async def create_test_agent():
    """Create a simple test agent with fake data tools."""
    
    # Load fake data to verify it exists
    load_fake_data()
    
    # Define tools
    tools = [
        search_emails,
        get_latest_emails,
        search_calendar,
        get_upcoming_events
    ]
    
    # System prompt
    system_prompt = f"""You are a helpful email and calendar assistant.

Current date: {CURRENT_DATE}

You have access to the following tools:
- search_emails: Search for emails containing specific keywords
- get_latest_emails: Get the most recent emails
- search_calendar: Search calendar events by query or date
- get_upcoming_events: Get upcoming calendar events

When the user asks a question:
1. Use the appropriate tools to gather information
2. Synthesize a clear, specific answer based on the tool results
3. Include relevant details like dates, times, subjects, and people

Be concise but informative."""

    # Create agent with middleware
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=[PhiToolResultMiddleware()]
    )
    
    # Create workflow
    def should_continue(state: TestState):
        """Determine if agent should continue or end."""
        messages = state.get("messages", [])
        if not messages:
            return END
        
        last_message = messages[-1]
        
        # Check if last message has tool calls
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.info(f"✅ Tool calls made ({len(last_message.tool_calls)}) - continuing")
            return "tools"
        
        logger.info("❌ No tool calls - ending")
        return END
    
    workflow = StateGraph(TestState)
    workflow.add_node("agent", agent)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue, ["tools", END])
    workflow.add_edge("tools", "agent")
    
    # Compile graph
    checkpointer = MemorySaver()
    graph = workflow.compile(checkpointer=checkpointer)
    
    return graph


async def test_agent():
    """Run test queries against the agent."""
    
    logger.info("\n" + "="*70)
    logger.info("PHI-4 FUNCTION CALLING TEST - Using Fake Data")
    logger.info("="*70)
    
    graph = await create_test_agent()
    
    # Test questions with expected answers
    test_cases = [
        {
            "question": "What are my latest emails about?",
            "expected": "Should list the 5 most recent emails from the test data, including subjects like 'Lunch Meeting Request', 'Project Alpha Update', 'Budget Review', etc."
        },
        {
            "question": "Find emails about meetings",
            "expected": "Should find emails containing 'meeting' keyword: 'Team Meeting Tomorrow', 'Lunch Meeting Request', and 'November 4th Meeting Confirmation'."
        },
        {
            "question": "What time is my meeting on November 4th?",
            "expected": "Should search calendar for November 4th and find the 'API Endpoints Discussion' meeting at 10:00 AM in Conference Room B."
        },
        {
            "question": "Do I have any events scheduled for November 27th?",
            "expected": "Should search calendar for 2025-11-27 and find 'Team Standup' at 09:00 AM and 'Q4 Planning Meeting' at 2:00 PM."
        },
        {
            "question": "Search for any emails from Bob",
            "expected": "Should find the email from bob@example.com with subject 'Budget Review - Action Required'."
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        question = test_case["question"]
        expected = test_case["expected"]
        
        logger.info(f"\n{'='*70}")
        logger.info(f"TEST {i}/{len(test_cases)}: {question}")
        logger.info(f"{'='*70}\n")
        
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=question)],
                "question": question
            },
            config={"configurable": {"thread_id": f"test-{i}"}}
        )
        
        # Extract final answer
        final_message = result["messages"][-1]
        phi_answer = final_message.content if hasattr(final_message, 'content') and final_message.content else "No answer generated"
        
        # Display results
        logger.info(f"\n📝 PHI-4 ANSWER:\n{phi_answer}\n")
        logger.info(f"🎯 EXPECTED:\n{expected}\n")
        
        # Small delay between tests
        await asyncio.sleep(1)
    
    logger.info("="*70)
    logger.info("✅ All tests complete!")
    logger.info("="*70)


if __name__ == "__main__":
    asyncio.run(test_agent())
