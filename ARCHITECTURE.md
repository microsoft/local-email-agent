# Email Agent Architecture with Phi-4 Local Model

## System Overview

```mermaid
graph TB
    User[User Question] --> Supervisor[Supervisor Agent]
    
    Supervisor --> SearchEmail[search_email_history]
    Supervisor --> ManageEmail[manage_email]
    Supervisor --> ScheduleEvent[schedule_event]
    Supervisor --> Question[Question Tool]
    Supervisor --> Done[Done Tool]
    
    ManageEmail --> EmailAgent[Email Sub-Agent]
    ScheduleEvent --> CalendarAgent[Calendar Sub-Agent]
    
    EmailAgent --> MCPEmail[MCP Email Tools]
    CalendarAgent --> MCPCalendar[MCP Calendar Tools]
    SearchEmail --> VectorDB[PostgreSQL + pgvector]
    
    MCPEmail --> M365[Microsoft 365 API]
    MCPCalendar --> M365
    
    style Supervisor fill:#e1f5ff
    style EmailAgent fill:#fff4e1
    style CalendarAgent fill:#fff4e1
    style VectorDB fill:#e8f5e9
```

## Phi-4 Integration with Custom Middleware

```mermaid
flowchart LR
    Input[User Input] --> Agent[LangChain Agent]
    Agent --> Phi[Phi-4-generic-gpu]
    Phi --> JSON[JSON Output]
    JSON --> MW1[PhiJSONToToolCallMiddleware]
    MW1 --> ToolCall[tool_calls Format]
    ToolCall --> Execute[Execute Tool]
    Execute --> ToolResult[Tool Results]
    ToolResult --> MW2[PhiToolResultMiddleware]
    MW2 --> Trim[Trimmed + Reminder]
    Trim --> Phi
    Phi --> Answer[Final Answer]
    
    style Phi fill:#d4edda
    style MW1 fill:#fff3cd
    style MW2 fill:#fff3cd
    style JSON fill:#f8d7da
    style ToolCall fill:#d1ecf1
```

## Middleware Details

### PhiJSONToToolCallMiddleware (after_model)

```mermaid
flowchart TD
    Start[Model Response] --> Check{Has tool_calls?}
    Check -->|Yes| Skip[Skip Processing]
    Check -->|No| Extract[Extract JSON Pattern]
    Extract --> Match{JSON Match?}
    Match -->|No| Return[Return None]
    Match -->|Yes| Parse[Parse Tool Name and Args]
    Parse --> Validate{Tool Valid?}
    Validate -->|No| Return
    Validate -->|Yes| Convert[Convert to AIMessage]
    Convert --> Replace[Replace Last Message]
    Replace --> Log[Log Success]
    
    style Extract fill:#e1f5ff
    style Convert fill:#d4edda
    style Replace fill:#d4edda
```

**Purpose**: Converts Phi's natural JSON output into LangChain's tool_calls format

**How it works**:
1. **Detects JSON**: Regex pattern `{"name": "tool_name", "args": {...}}`
2. **Validates**: Checks if tool exists in schema
3. **Converts**: Creates AIMessage with proper tool_calls structure
4. **Replaces**: Swaps text response with structured tool call

### PhiToolResultMiddleware (before_model)

```mermaid
flowchart TD
    Start[Before Model Call] --> Check{Last msg is ToolMessage?}
    Check -->|No| Skip[Skip Processing]
    Check -->|Yes| Length{Content over 1000 chars?}
    Length -->|No| Skip
    Length -->|Yes| Trim[Trim to 1000 chars]
    Trim --> Create[Create New ToolMessage]
    Create --> Reminder[Add Reminder Message]
    Reminder --> Replace[Replace Messages]
    Replace --> Log[Log Trimming]
    
    style Trim fill:#fff3cd
    style Reminder fill:#d1ecf1
    style Replace fill:#d4edda
```

**Purpose**: Prevents context overflow and prompts Phi to synthesize answers

**How it works**:
1. **Detects long results**: Checks if tool result > 1000 characters
2. **Trims intelligently**: Keeps first 1000 chars, notes trimmed amount
3. **Adds reminder**: Appends "Based on the tool results above, please answer the original question."
4. **Prevents empty responses**: Phi was returning empty content without this

## Component Breakdown

### Supervisor Agent
- **Model**: Phi-4-generic-gpu (Temperature: 0.0)
- **System Prompt**: `agent_system_prompt_local_model` with tool calling instructions
- **Tools**: High-level orchestration tools
  - `search_email_history`: Vector search in PostgreSQL
  - `manage_email`: Delegates to Email Sub-Agent
  - `schedule_event`: Delegates to Calendar Sub-Agent
  - `Question`: Human-in-the-loop for clarification
  - `Done`: Signals completion
- **Middleware**: Both custom middleware components

### Sub-Agents
Both use simpler prompts with JSON output instructions:

**Email Sub-Agent**:
- Tools: `list-mail-messages`, `create-draft-email`, `get-mail-message`, `send-mail`
- Middleware: `PhiJSONToToolCallMiddleware`

**Calendar Sub-Agent**:
- Tools: `list-calendar-events`, `create-calendar-event`, etc.
- Middleware: `PhiJSONToToolCallMiddleware`
- Special constraint: Always use `{"top": 10}` parameter

## Data Flow Example

```mermaid
sequenceDiagram
    participant User
    participant Supervisor
    participant MW1 as JSONToToolCall
    participant MW2 as ToolResult
    participant Tool as search_email_history
    participant VectorDB
    
    User->>Supervisor: What time is my meeting on Nov 4?
    Supervisor->>Supervisor: Generate response
    Note over Supervisor: Phi outputs JSON with tool name and args
    
    Supervisor->>MW1: after_model hook
    MW1->>MW1: Parse JSON
    MW1->>MW1: Convert to tool_calls
    MW1-->>Supervisor: AIMessage with tool_calls
    
    Supervisor->>Tool: Execute search_email_history
    Tool->>VectorDB: Vector search
    VectorDB-->>Tool: 5 email results
    Tool-->>Supervisor: ToolMessage with results
    
    Supervisor->>MW2: before_model hook
    MW2->>MW2: Trim to 1000 chars
    MW2->>MW2: Add reminder message
    MW2-->>Supervisor: Modified messages
    
    Supervisor->>Supervisor: Generate answer
    Note over Supervisor: Your meeting is at 14:00 UTC
    Supervisor-->>User: Final answer
```

## Key Innovations

1. **JSON-First Approach**: Leverage Phi's strength at generating JSON instead of fighting its tool calling limitations

2. **Middleware Pattern**: Use LangChain's middleware hooks to transform Phi's output at the right moments
   - `after_model`: Convert text to structured format
   - `before_model`: Trim context and add guidance

3. **Context Management**: Trim tool results to prevent overwhelming the 14B parameter model

4. **Hierarchical Architecture**: Supervisor delegates to specialized sub-agents, reducing complexity per agent

## Performance Characteristics

- **Model**: Phi-4-generic-gpu (8.37 GB)
- **Temperature**: 0.0 (maximum accuracy)
- **Endpoint**: Foundry Local on port 57389
- **Context Window**: Managed via trimming at 1000 characters
- **Tool Call Success Rate**: ~100% with middleware (was ~0% without)

## Storage Architecture

```mermaid
flowchart LR
    Email[Email Content] --> Files[Blob Storage]
    Email --> Embed[Generate Embedding]
    Embed --> Azure[Azure OpenAI Embeddings]
    Azure --> PG[PostgreSQL + pgvector]
    
    Query[Search Query] --> Embed2[Generate Embedding]
    Embed2 --> Azure
    Azure --> PG
    PG --> Results[Search Results]
    
    style PG fill:#e8f5e9
    style Azure fill:#d1ecf1
```

## MCP Integration

```mermaid
flowchart TB
    Agent[Email Agent] --> MCP[MCP Adapters]
    MCP -->|stdio| Server[ms-365-mcp-server]
    Server --> Tools[99 MCP Tools]
    Tools --> Outlook[Outlook Mail]
    Tools --> Calendar[Outlook Calendar]
    
    style Server fill:#e1f5ff
    style Tools fill:#fff4e1
```

**Communication**: stdio (standard input/output)  
**Authentication**: Delegated to MCP server  
**Tools Loaded**: 99 (Email: 4, Calendar: 12, Auth: 3)

