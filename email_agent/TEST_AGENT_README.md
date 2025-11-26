# Test Phi-4 Agent - Fake Data Testing

A simplified test email agent to verify Phi-4's function calling capabilities. This agent should uses langchain-openai to be able to make an agent that should use the fake email and calendar data to answer the test questions.  

## Purpose

This test agent helps you:
- **Verify Phi-4 function calling works** - See if tools are actually called vs. hallucinated
- **Test middleware** - Ensure `PhiToolResultMiddleware` is working correctly  
- **Understand expected behavior** - Compare Phi's output against expected results

## 📁 What's Included

```
test_phi4_agent.py           # Main test agent file
test_data/
├── emails.json              # Fake email data (5 emails)
└── calendar.json            # Fake calendar data (4 events)
```

## Quick Start

1. **Install Foundry Local:**

   - **Windows**: Install Foundry Local for your architecture (x64 or arm64):

     ```bash
       winget install Microsoft.FoundryLocal
     ```

   - **MacOS**: Open a terminal and run the following command:
    ```bash
    brew install microsoft/foundrylocal/foundrylocal
    ```
  Alternatively, you can download the installers from the [releases page](https://github.com/microsoft/Foundry-Local/releases) and follow the on-screen installation instructions.

> [!TIP]
> For any issues, refer to the [Installation section](#installing) below.

2. **Install Python packages**: Open a terminal and run the following command to run a model:

   i. Create a virtual environment  
   ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```
    
    ii. Install the required packages 
    ```bash
    pip install -r email_agent/test_requirements.txt
    ```

3. Run the Test Code

This test agent checks if Phi-4 is using function calling as expected to answer the questions at the bottom of the `email_agent.test_phi4_agent.py` file.  

```bash
# Make sure Foundry Local is running on http://127.0.0.1:63911
# Then simply run:
python3 -m email_agent.test_phi4_agent
```

That's it! No database, no configuration files, no authentication.

## 📊 What It Tests

The test suite runs 5 queries against fake data:

| Test | Question | Expected Behavior |
|------|----------|-------------------|
| 1 | "What are my latest emails about?" | Should call `get_latest_emails(count=5)` and list 5 emails |
| 2 | "Find emails about meetings" | Should call `search_emails(query="meetings")` and find 3 emails |
| 3 | "What time is my meeting on November 4th?" | Should call `search_calendar(date="2025-11-04")` and find 10:00 AM meeting |
| 4 | "Do I have any events scheduled for November 27th?" | Should call `search_calendar(date="2025-11-27")` and find 2 events |
| 5 | "Search for any emails from Bob" | Should call `search_emails(query="Bob")` and find budget email |

## 📝 Understanding the Output

### Success - Tools Called ✅

```
INFO:__main__:✅ Tool calls made (1) - continuing
INFO:__main__:🔍 Searching emails for: meetings
INFO:__main__:💡 Added synthesis reminder after tool result

📝 PHI-4 ANSWER:
I found 3 emails about meetings:
1. From alice@example.com - Team Meeting Tomorrow at 2 PM
2. From dave@example.com - November 4th Meeting at 10 AM in Conference Room B
3. From eve@example.com - Lunch Meeting Request

🎯 EXPECTED:
Should find emails containing 'meeting' keyword...
```

### Failure - No Tools Called ❌

```
INFO:__main__:❌ No tool calls - ending

📝 PHI-4 ANSWER:
[Using the tool: search_emails with keywords "meeting"]
I will search your emails for messages about meetings...

🎯 EXPECTED:
Should find emails containing 'meeting' keyword...
```

**Problem**: Phi-4 is *describing* what it would do instead of actually calling the tool. This means function calling isn't working properly.

## 🛠️ Available Tools

The test agent provides 4 simple tools:

### 1. `search_emails(query: str, max_results: int = 5)`
Search emails by keyword in subject, body, or sender.

```python
# Example: Find emails about "budget"
search_emails(query="budget", max_results=5)
```

### 2. `get_latest_emails(count: int = 5)`
Get the most recent emails by date.

```python
# Example: Get 3 most recent emails
get_latest_emails(count=3)
```

### 3. `search_calendar(query: str = None, date: str = None)`
Search calendar events by keyword or specific date.

```python
# Example: Find events on Nov 4th
search_calendar(date="2025-11-04")

# Example: Find meetings
search_calendar(query="meeting")
```

### 4. `get_upcoming_events(days: int = 7)`
Get all upcoming events (sorted by date).

```python
# Example: Get next 7 days of events
get_upcoming_events(days=7)
```

## 📂 Test Data

### Emails (`test_data/emails.json`)

Contains 5 sample emails:
- Team Meeting Tomorrow (from alice@example.com)
- Budget Review (from bob@example.com)
- Project Alpha Update (from carol@example.com)
- November 4th Meeting Confirmation (from dave@example.com)
- Lunch Meeting Request (from eve@example.com)

### Calendar (`test_data/calendar.json`)

Contains 4 sample events:
- Team Standup (Nov 27, 9:00 AM)
- Q4 Planning Meeting (Nov 27, 2:00 PM)
- API Endpoints Discussion (Nov 4, 10:00 AM)
- Budget Review Call (Nov 28, 3:00 PM)

**You can edit these files** to test different scenarios!

## 🔧 How It Works

### 1. Load Fake Data
```python
# Loads from test_data/emails.json and test_data/calendar.json
load_fake_data()
```

### 2. Create LLM with Middleware
```python
llm = ChatOpenAI(
    base_url=foundry_manager.endpoint,
    model="Phi-4-generic-gpu",
    temperature=0.0
)

agent = create_agent(
    model=llm,
    tools=[search_emails, get_latest_emails, search_calendar, get_upcoming_events],
    middleware=[PhiToolResultMiddleware()]  # Only result trimming middleware
)
```

### 3. Run Test Questions
```python
result = await graph.ainvoke({
    "messages": [HumanMessage(content=question)],
    "question": question
})
```

### 4. Check Results
- ✅ **Tool calls made** - Function calling works!
- ❌ **No tool calls** - Phi-4 is hallucinating tool usage



**Quick Test**: `python3 -m email_agent.test_phi4_agent` 🚀
