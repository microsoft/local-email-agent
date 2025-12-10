"""FastAPI Backend for Agent Inbox.

This module provides REST API endpoints for the Agent Inbox UI,
enabling thread management and human-in-the-loop interactions.

Endpoints:
    GET  /health              - Health check
    GET  /threads             - List all threads (with status filter)
    GET  /threads/{id}        - Get thread details
    GET  /threads/{id}/state  - Get full thread state
    POST /threads/{id}/resume - Resume a thread with human response
    POST /runs                - Start a new agent run
    POST /runs/stream         - Start a new agent run with SSE streaming

Usage:
    uvicorn email_agent.api:app --reload --port 8000
"""

import asyncio
import datetime
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from email_agent.foundry_service import foundry_health_check
from email_agent.hitl_schemas import HumanInterrupt, HumanResponse, create_interrupt

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Models for API
# ============================================================================

class ActionRequestModel(BaseModel):
    """Request for an action to be performed."""
    action: str
    args: Dict[str, Any]


class HumanInterruptConfigModel(BaseModel):
    """Configuration for what actions the human can take."""
    allow_ignore: bool
    allow_respond: bool
    allow_edit: bool
    allow_accept: bool


class HumanInterruptModel(BaseModel):
    """Interrupt data sent to the UI for human review."""
    action_request: ActionRequestModel
    config: HumanInterruptConfigModel
    description: Optional[str] = None


class RunRequest(BaseModel):
    """Request to start a new agent run."""
    question: Optional[str] = Field(None, description="User question to process")
    email_input: Optional[str] = Field(None, description="Email content to process")
    thread_id: Optional[str] = Field(None, description="Existing thread ID to continue")


class ResumeRequest(BaseModel):
    """Request to resume an interrupted thread."""
    type: Literal["accept", "ignore", "response", "edit"] = Field(
        ..., description="Type of response"
    )
    args: Optional[Any] = Field(
        None, description="Response arguments (depends on type)"
    )


class ThreadSummary(BaseModel):
    """Summary of a thread for list view."""
    thread_id: str
    status: Literal["interrupted", "idle", "busy", "error"]
    created_at: str
    updated_at: str
    question: Optional[str] = None
    interrupt_description: Optional[str] = None


class ThreadDetail(BaseModel):
    """Detailed thread information."""
    thread_id: str
    status: Literal["interrupted", "idle", "busy", "error"]
    created_at: str
    updated_at: str
    messages: List[Dict[str, Any]]
    interrupt: Optional[HumanInterruptModel] = None


class RunResponse(BaseModel):
    """Response from starting a run."""
    thread_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    interrupt: Optional[HumanInterruptModel] = None


# ============================================================================
# Thread Storage (In-memory for now, will add Postgres later)
# ============================================================================

class ThreadStore:
    """In-memory thread storage. Will be replaced with Postgres."""
    
    def __init__(self):
        self.threads: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
    
    async def create_thread(self, thread_id: str, question: Optional[str] = None) -> Dict[str, Any]:
        """Create a new thread."""
        async with self._lock:
            now = datetime.datetime.now(datetime.UTC).isoformat()
            thread = {
                "thread_id": thread_id,
                "status": "idle",
                "created_at": now,
                "updated_at": now,
                "question": question,
                "messages": [],
                "interrupt": None,
                "state": None
            }
            self.threads[thread_id] = thread
            return thread
    
    async def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """Get a thread by ID."""
        return self.threads.get(thread_id)
    
    async def update_thread(self, thread_id: str, **updates) -> Dict[str, Any]:
        """Update a thread."""
        async with self._lock:
            if thread_id not in self.threads:
                raise ValueError(f"Thread {thread_id} not found")
            
            self.threads[thread_id].update(updates)
            self.threads[thread_id]["updated_at"] = datetime.datetime.now(datetime.UTC).isoformat()
            return self.threads[thread_id]
    
    async def list_threads(
        self, 
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """List threads, optionally filtered by status."""
        threads = list(self.threads.values())
        
        if status:
            threads = [t for t in threads if t["status"] == status]
        
        # Sort by updated_at descending
        threads.sort(key=lambda t: t["updated_at"], reverse=True)
        
        return threads[:limit]
    
    async def delete_thread(self, thread_id: str) -> bool:
        """Delete a thread by ID."""
        async with self._lock:
            if thread_id in self.threads:
                del self.threads[thread_id]
                return True
            return False


# Global thread store
thread_store = ThreadStore()


# ============================================================================
# Agent Graph (lazy-loaded)
# ============================================================================

_graph = None
_graph_lock = asyncio.Lock()


async def get_graph():
    """Get the agent graph (lazy initialization)."""
    global _graph
    
    if _graph is not None:
        return _graph
    
    async with _graph_lock:
        if _graph is not None:
            return _graph
        
        logger.info("🔧 Initializing agent graph...")
        
        # Import here to avoid circular imports and ensure Foundry is ready
        from email_agent.agent_graph import create_agent_graph
        
        _graph = await create_agent_graph()
        logger.info("✅ Agent graph initialized")
        
        return _graph


# ============================================================================
# FastAPI App
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("🚀 Starting Agent Inbox API...")
    
    # Pre-warm Foundry Local (optional, can be lazy)
    try:
        health = foundry_health_check()
        if health["ready"]:
            logger.info(f"✅ Foundry Local ready at {health['endpoint']}")
        else:
            logger.warning(f"⚠️ Foundry Local not ready: {health.get('error')}")
    except Exception as e:
        logger.warning(f"⚠️ Foundry Local check failed: {e}")
    
    yield
    
    # Shutdown
    logger.info("👋 Shutting down Agent Inbox API...")


app = FastAPI(
    title="Agent Inbox API",
    description="REST API for the Email Agent Inbox with Human-in-the-Loop",
    version="1.0.0",
    lifespan=lifespan
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    foundry_status = foundry_health_check()
    
    return {
        "status": "healthy",
        "foundry": foundry_status,
        "threads_count": len(thread_store.threads)
    }


@app.get("/threads", response_model=List[ThreadSummary])
async def list_threads(
    status: Optional[Literal["interrupted", "idle", "busy", "error"]] = Query(None),
    limit: int = Query(50, ge=1, le=100)
):
    """List all threads, optionally filtered by status."""
    threads = await thread_store.list_threads(status=status, limit=limit)
    
    return [
        ThreadSummary(
            thread_id=t["thread_id"],
            status=t["status"],
            created_at=t["created_at"],
            updated_at=t["updated_at"],
            question=t.get("question"),
            interrupt_description=t["interrupt"]["description"] if t.get("interrupt") else None
        )
        for t in threads
    ]


@app.get("/threads/{thread_id}", response_model=ThreadDetail)
async def get_thread(thread_id: str):
    """Get detailed information about a thread."""
    thread = await thread_store.get_thread(thread_id)
    
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    
    return ThreadDetail(
        thread_id=thread["thread_id"],
        status=thread["status"],
        created_at=thread["created_at"],
        updated_at=thread["updated_at"],
        messages=thread.get("messages", []),
        interrupt=thread.get("interrupt")
    )


@app.get("/threads/{thread_id}/state")
async def get_thread_state(thread_id: str):
    """Get the full state of a thread."""
    thread = await thread_store.get_thread(thread_id)
    
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    
    return {
        "thread_id": thread_id,
        "state": thread.get("state", {}),
        "interrupt": thread.get("interrupt")
    }


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Delete a thread."""
    deleted = await thread_store.delete_thread(thread_id)
    
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    
    return {"status": "deleted", "thread_id": thread_id}


@app.post("/runs", response_model=RunResponse)
async def create_run(request: RunRequest):
    """Start a new agent run."""
    # Generate thread ID if not provided
    thread_id = request.thread_id or f"thread_{uuid.uuid4().hex[:12]}"
    
    # Create or get thread
    thread = await thread_store.get_thread(thread_id)
    if not thread:
        thread = await thread_store.create_thread(
            thread_id=thread_id,
            question=request.question
        )
    
    # Update status to busy
    await thread_store.update_thread(thread_id, status="busy")
    
    try:
        # Get the agent graph
        graph = await get_graph()
        
        # Prepare input
        input_data = {
            "question": request.question,
            "email_input": request.email_input,
            "classification_decision": None
        }
        
        # Run the graph
        config = {"configurable": {"thread_id": thread_id}}
        
        try:
            result = await graph.ainvoke(input_data, config=config)
            
            # Check for interrupt
            # LangGraph stores interrupt data in the graph state when interrupted
            state = await graph.aget_state(config)
            
            if state.next:  # Graph is paused (interrupted)
                # Extract interrupt data from state
                interrupt_data: Optional[HumanInterrupt] = None
                if hasattr(state, 'tasks') and state.tasks:
                    for task in state.tasks:
                        if hasattr(task, 'interrupts') and task.interrupts:
                            interrupt_value = task.interrupts[0].value
                            if isinstance(interrupt_value, dict) and "action_request" in interrupt_value:
                                interrupt_data = interrupt_value
                            else:
                                # Convert old-style interrupt to new format
                                interrupt_data = create_interrupt(
                                    action="unknown",
                                    args={},
                                    description=str(interrupt_value)
                                )
                
                await thread_store.update_thread(
                    thread_id,
                    status="interrupted",
                    interrupt=interrupt_data,
                    state=result,
                    messages=_extract_messages(result)
                )
                
                return RunResponse(
                    thread_id=thread_id,
                    status="interrupted",
                    interrupt=interrupt_data
                )
            
            # Graph completed successfully
            await thread_store.update_thread(
                thread_id,
                status="idle",
                interrupt=None,
                state=result,
                messages=_extract_messages(result)
            )
            
            return RunResponse(
                thread_id=thread_id,
                status="completed",
                result={"messages": _extract_messages(result)}
            )
            
        except Exception as e:
            logger.error(f"Graph execution error: {e}")
            await thread_store.update_thread(thread_id, status="error")
            raise HTTPException(status_code=500, detail=str(e))
            
    except Exception as e:
        logger.error(f"Run error: {e}")
        await thread_store.update_thread(thread_id, status="error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/threads/{thread_id}/resume", response_model=RunResponse)
async def resume_thread(thread_id: str, request: ResumeRequest):
    """Resume an interrupted thread with a human response."""
    thread = await thread_store.get_thread(thread_id)
    
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    
    if thread["status"] != "interrupted":
        raise HTTPException(
            status_code=400, 
            detail=f"Thread {thread_id} is not interrupted (status: {thread['status']})"
        )
    
    # Update status to busy
    await thread_store.update_thread(thread_id, status="busy")
    
    try:
        graph = await get_graph()
        config = {"configurable": {"thread_id": thread_id}}
        
        # Build the response to send back to the graph
        human_response: HumanResponse = {
            "type": request.type,
            "args": request.args
        }
        
        # Resume the graph with the response
        # LangGraph expects Command(resume=value) to be passed as the input when resuming
        from langgraph.types import Command
        result = await graph.ainvoke(
            Command(resume=human_response),
            config=config
        )
        
        # Check if interrupted again
        state = await graph.aget_state(config)
        
        if state.next:
            # Still interrupted (possibly different action)
            interrupt_data: Optional[HumanInterrupt] = None
            if hasattr(state, 'tasks') and state.tasks:
                for task in state.tasks:
                    if hasattr(task, 'interrupts') and task.interrupts:
                        interrupt_value = task.interrupts[0].value
                        if isinstance(interrupt_value, dict) and "action_request" in interrupt_value:
                            interrupt_data = interrupt_value
            
            await thread_store.update_thread(
                thread_id,
                status="interrupted",
                interrupt=interrupt_data,
                state=result,
                messages=_extract_messages(result)
            )
            
            return RunResponse(
                thread_id=thread_id,
                status="interrupted",
                interrupt=interrupt_data
            )
        
        # Completed
        await thread_store.update_thread(
            thread_id,
            status="idle",
            interrupt=None,
            state=result,
            messages=_extract_messages(result)
        )
        
        return RunResponse(
            thread_id=thread_id,
            status="completed",
            result={"messages": _extract_messages(result)}
        )
        
    except Exception as e:
        logger.error(f"Resume error: {e}")
        await thread_store.update_thread(thread_id, status="error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/threads/{thread_id}/resume/stream")
async def resume_thread_stream(thread_id: str, request: ResumeRequest):
    """Resume an interrupted thread with SSE streaming.
    
    Returns a stream of server-sent events showing the agent's progress
    after the human response is processed.
    """
    thread = await thread_store.get_thread(thread_id)
    
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    
    if thread["status"] != "interrupted":
        raise HTTPException(
            status_code=400, 
            detail=f"Thread {thread_id} is not interrupted (status: {thread['status']})"
        )
    
    # Update status to busy
    await thread_store.update_thread(thread_id, status="busy")
    
    # Get the graph
    graph = await get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    
    # Build the response to send back to the graph
    human_response: HumanResponse = {
        "type": request.type,
        "args": request.args
    }
    
    # Use Command(resume=...) as the input for resuming
    from langgraph.types import Command
    input_data = Command(resume=human_response)
    
    # Return streaming response
    return StreamingResponse(
        _run_graph_with_streaming(graph, input_data, config, thread_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
            "Content-Encoding": "identity",
        }
    )


# ============================================================================
# SSE Streaming Endpoint
# ============================================================================

def _sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """Format a server-sent event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _run_graph_with_streaming(
    graph,
    input_data: Dict[str, Any],
    config: Dict[str, Any],
    thread_id: str
) -> AsyncGenerator[str, None]:
    """Run the graph and yield SSE events for each step."""
    try:
        # Yield start event immediately
        yield _sse_event("start", {"thread_id": thread_id, "status": "running"})
        await asyncio.sleep(0)  # Force flush
        
        # Use astream_events to get detailed streaming from LangGraph
        step_count = 0
        
        # Track emitted tool calls to avoid duplicates
        # Key: (tool_name, frozenset of args items) -> emitted
        emitted_tool_calls: set = set()
        
        async for event in graph.astream_events(input_data, config=config, version="v2"):
            event_kind = event.get("event")
            event_name = event.get("name", "")
            event_data = event.get("data", {})
            
            # Debug log all events to understand what's coming through
            logger.debug(f"SSE Event: {event_kind} | {event_name}")
            
            # Track ANY chain that produces tool calls (not just "supervisor")
            # Only emit from on_chain_end to avoid duplicates from on_tool_start
            if event_kind == "on_chain_end":
                output = event_data.get("output", {})
                
                # Handle dict output with messages
                if isinstance(output, dict):
                    messages = output.get("messages", [])
                    for msg in messages:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tool_name = tc.get("name")
                                tool_args = tc.get("args", {})
                                
                                # Create a unique key for this tool call
                                # Convert args to a hashable format
                                try:
                                    args_key = tuple(sorted(tool_args.items())) if tool_args else ()
                                except TypeError:
                                    # If args contain unhashable types, use string repr
                                    args_key = str(tool_args)
                                
                                call_key = (tool_name, args_key)
                                
                                # Skip if we've already emitted this exact tool call
                                if call_key in emitted_tool_calls:
                                    logger.debug(f"📡 Skipping duplicate tool_call: {tool_name}")
                                    continue
                                
                                emitted_tool_calls.add(call_key)
                                step_count += 1
                                logger.info(f"📡 Streaming tool_call: {tool_name}")
                                
                                yield _sse_event("tool_call", {
                                    "step": step_count,
                                    "tool": tool_name,
                                    "args": tool_args,
                                    "description": f"Calling {tool_name}..."
                                })
                                await asyncio.sleep(0)  # Force flush
            
            # Track tool execution results
            if event_kind == "on_tool_end":
                tool_output = event_data.get("output", "")
                tool_name = event_name
                
                # Send more of the output for visibility in activity panel
                output_preview = str(tool_output)[:2000]
                if len(str(tool_output)) > 2000:
                    output_preview += "..."
                
                logger.info(f"📡 Streaming tool_result: {tool_name}")
                
                yield _sse_event("tool_result", {
                    "step": step_count,
                    "tool": tool_name,
                    "result": output_preview
                })
                await asyncio.sleep(0)  # Force flush
            
            # Note: on_tool_start is intentionally NOT emitting tool_call events
            # because we already capture them in on_chain_end. This avoids duplicates.
            # We just log for debugging:
            if event_kind == "on_tool_start":
                tool_name = event_name
                logger.debug(f"📡 Tool starting (no SSE): {tool_name}")
            
            # Note: on_chat_model_start events are not emitted as tool_calls
            # to avoid cluttering the activity panel. LLM calls happen frequently.
            if event_kind == "on_chat_model_start":
                logger.info(f"📡 Streaming llm_start: {event_name}")
        
        # Get final state to check for interrupts
        state = await graph.aget_state(config)
        
        if state.next:  # Graph is paused (interrupted)
            interrupt_data: Optional[HumanInterrupt] = None
            if hasattr(state, 'tasks') and state.tasks:
                for task in state.tasks:
                    if hasattr(task, 'interrupts') and task.interrupts:
                        interrupt_value = task.interrupts[0].value
                        if isinstance(interrupt_value, dict) and "action_request" in interrupt_value:
                            interrupt_data = interrupt_value
                        else:
                            interrupt_data = create_interrupt(
                                action="unknown",
                                args={},
                                description=str(interrupt_value)
                            )
            
            # Get current state values
            state_values = state.values if hasattr(state, 'values') else {}
            
            await thread_store.update_thread(
                thread_id,
                status="interrupted",
                interrupt=interrupt_data,
                state=state_values,
                messages=_extract_messages(state_values)
            )
            
            yield _sse_event("interrupt", {
                "thread_id": thread_id,
                "status": "interrupted",
                "interrupt": interrupt_data
            })
        else:
            # Graph completed - get the final result
            state_values = state.values if hasattr(state, 'values') else {}
            messages = _extract_messages(state_values)
            
            # Find the final answer from Done tool or last AI message
            final_answer = None
            for msg in reversed(messages):
                if msg.get("type") == "AIMessage" and msg.get("tool_calls"):
                    for tc in msg.get("tool_calls", []):
                        if tc.get("name") == "Done":
                            final_answer = tc.get("args", {}).get("answer")
                            break
                if final_answer:
                    break
            
            if not final_answer:
                # Fall back to last message content or tool result
                for msg in reversed(messages):
                    if msg.get("content"):
                        final_answer = msg.get("content")
                        break
            
            await thread_store.update_thread(
                thread_id,
                status="idle",
                interrupt=None,
                state=state_values,
                messages=messages
            )
            
            yield _sse_event("done", {
                "thread_id": thread_id,
                "status": "completed",
                "answer": final_answer or "Task completed.",
                "messages": messages
            })
            
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        await thread_store.update_thread(thread_id, status="error")
        yield _sse_event("error", {
            "thread_id": thread_id,
            "status": "error",
            "error": str(e)
        })


@app.post("/runs/stream")
async def create_run_stream(request: RunRequest):
    """Start a new agent run with SSE streaming.
    
    Returns a stream of server-sent events:
    - start: Run has started
    - tool_call: A tool is being called
    - tool_result: Tool returned a result
    - sub_agent_start: Sub-agent started
    - sub_agent_result: Sub-agent completed
    - interrupt: HITL interrupt required
    - done: Run completed with final answer
    - error: An error occurred
    """
    # Create or get thread
    if request.thread_id:
        thread_id = request.thread_id
        thread = await thread_store.get_thread(thread_id)
        if not thread:
            # Create the thread if it doesn't exist
            await thread_store.create_thread(thread_id, request.question)
    else:
        thread_id = str(uuid.uuid4())
        await thread_store.create_thread(thread_id, request.question)
    
    # Update status to busy
    await thread_store.update_thread(thread_id, status="busy")
    
    # Get the graph
    graph = await get_graph()
    
    # Build input data
    input_data = {
        "question": request.question,
        "email_input": request.email_input,
        "classification_decision": None
    }
    
    config = {"configurable": {"thread_id": thread_id}}
    
    # Return streaming response
    return StreamingResponse(
        _run_graph_with_streaming(graph, input_data, config, thread_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Transfer-Encoding": "chunked",
            "Content-Encoding": "identity",  # Prevent compression buffering
        }
    )


def _extract_messages(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract messages from state for API response."""
    messages = state.get("messages", [])
    result = []
    
    for msg in messages:
        msg_dict = {
            "type": type(msg).__name__,
            "content": getattr(msg, "content", "")
        }
        
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            msg_dict["tool_calls"] = msg.tool_calls
        
        if hasattr(msg, "name"):
            msg_dict["name"] = msg.name
            
        result.append(msg_dict)
    
    return result


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
