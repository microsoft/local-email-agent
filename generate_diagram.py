"""Generate architecture diagram for the Email Agent project."""
from diagrams import Cluster, Diagram, Edge
from diagrams.generic.blank import Blank

# Graph attributes for better layout
graph_attr = {
    "fontsize": "16",
    "bgcolor": "white",
    "splines": "ortho",
    "nodesep": "1.0",
    "ranksep": "1.5"
}

with Diagram("Email Agent Architecture", 
             filename="images/architecture",
             direction="LR",
             graph_attr=graph_attr,
             show=False):
    
    # User/Client
    user = Blank("User")
    
    # Frontend Cluster
    with Cluster("Frontend (Next.js)"):
        frontend = Blank("React UI\nComponents")
        streaming = Blank("Streaming Manager\n(SSE Handler)")
        frontend >> streaming
    
    # Backend Cluster
    with Cluster("Backend (FastAPI)"):
        api = Blank("API Endpoints")
        
        with Cluster("LangGraph Orchestrator"):
            graph = Blank("State Graph")
            checkpoint = Blank("Human-in-the-Loop\nCheckpointer")
            
            with Cluster("Agent Logic"):
                supervisor = Blank("Supervisor Agent")
                email_agent = Blank("Email Sub-Agent")
                rag_agent = Blank("RAG Sub-Agent")
        
        api >> graph >> checkpoint
        graph >> supervisor
        supervisor >> Edge(label="route") >> email_agent
        supervisor >> Edge(label="route") >> rag_agent
    
    # Infrastructure & Services
    with Cluster("Infrastructure & Services"):
        llm = Blank("Phi-4 Model\n(Foundry Local)")
        mcp = Blank("Microsoft 365\nMCP Server")
        db = Blank("PostgreSQL\npgvector")
        m365 = Blank("Microsoft 365\n(Graph API)")
    
    # Connections
    user >> frontend
    streaming >> Edge(label="HTTP/SSE") >> api
    
    # Agent to LLM (inference)
    supervisor >> Edge(label="inference", style="dashed") >> llm
    email_agent >> Edge(label="inference", style="dashed") >> llm
    rag_agent >> Edge(label="inference", style="dashed") >> llm
    
    # Tool usage
    email_agent >> Edge(label="MCP Protocol") >> mcp
    rag_agent >> Edge(label="Vector Search") >> db
    
    # External
    mcp >> Edge(label="Graph API", style="dotted") >> m365
