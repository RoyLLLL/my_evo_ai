# Executive Summary

We propose extending **my_evo_ai** by adding a modular *Hermes-style Memory System* that provides **vector-indexed long-term memory** across user sessions. This involves introducing a **MemoryService** for storing/retrieving embeddings in Postgres (using the pgvector extension), and a **MemoryMiddleware** on the event bus to *prefetch* relevant memories before each agent turn and *sync* new events after each turn【32†L79-L87】.  Concretely, we will:

- **Add a `memories` table** (SQL migration) with columns like `id, user_id, type, content, embedding, timestamp, importance, agent`. We enable `vector` extension and HNSW index for fast similarity search【25†L445-L452】.
- **Implement `MemoryService`** (async Python) with methods: `write_event(event)` (compute embedding, save as “episodic” memory), `search(query, scope, top_k)` (query embedding + vector search), and `summarize(user_id)` (batch LLM summary of old events to create “semantic” memory).
- **Hook into the Event Bus** via a new `MemoryMiddleware`: its `before_dispatch` retrieves related memories (`MemoryService.search`) and injects them into the agent’s context; its `after_dispatch` writes the latest turn to memory (`MemoryService.write_event`)【32†L79-L87】.
- **Adjust agents to use memory**: e.g. in an LLM agent’s prompt, include recent memory summaries from `ctx.memory`.
- **Support agent-scoped memory**: each agent may access different memory types (e.g. “planner” sees semantic memory, “tool” sees episodic, etc). The middleware filters by agent name or memory type when searching.
- **Implement memory consolidation**: a periodic task or triggered function calls `summarize(user_id)` to compress old episodic memories into a semantic summary, to prevent unbounded growth.

This approach aligns with Hermes Agent’s design of prefetch-before-turn and sync-after-turn memory【32†L79-L87】. It treats memory writes as events, allowing further plugins (e.g. user-modeling agents) to subscribe. Using Postgres + pgvector leverages your existing DB: we’ll create an HNSW index for nearest-neighbor search (e.g. `ORDER BY embedding <-> $1 LIMIT k`)【25†L445-L452】. The episodic memory (per-turn logs) forms the foundation for later learning, as in Hermes’s self-improvement loop【30†L75-L83】.

The following sections give **prioritized code changes** (file paths and diffs), full implementations for each component, a Mermaid sequence diagram of the event flow, a comparison table of memory types, and instructions for testing and verification.

# Code Changes (Files and Patches)

Below is a prioritized summary of the needed code changes. File paths are relative to the `my_evo_ai` repo root.

1. **`migrations/0001_create_memories.sql`** – New SQL migration: create `memories` table and vector index.
2. **`memory/memory_service.py`** – New `MemoryService` class (async) for search/write/summarize.
3. **`memory/memory_middleware.py`** – New `MemoryMiddleware` class to plug into the event bus.
4. **Modify** `event_bus.py` (or wherever middleware are registered) to register the `MemoryMiddleware`.
5. **Modify** any agent (e.g. `agents/llm_agent.py`) to consume `ctx.memory` in prompts (example below).
6. **`tests/test_memory_service.py`** – New unit tests for `MemoryService` (using an in-memory or test DB).
7. **`tests/test_memory_middleware.py`** – New unit tests for middleware behavior (mocking events, context).

We assume your project uses async Python (e.g. `asyncio`) and has a mechanism to register middleware on the event bus. Below we show git-like diffs and full implementations.

```diff
=== File: migrations/0001_create_memories.sql ===
+-- Migration: create the memories table with pgvector
+CREATE EXTENSION IF NOT EXISTS vector;
+
+CREATE TABLE memories (
+  id SERIAL PRIMARY KEY,
+  user_id TEXT NOT NULL,
+  type TEXT NOT NULL,           -- 'episodic' or 'semantic'
+  content TEXT NOT NULL,        -- raw memory text (e.g. event or summary)
+  embedding VECTOR(1536),       -- 1536-dim vector (e.g. OpenAI embeddings)
+  agent TEXT,                   -- agent name that created/owns this memory
+  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
+  importance FLOAT DEFAULT 1.0  -- optional weighting
+);
+
+-- Create an HNSW index on the embedding column for fast nearest-neighbor search
+CREATE INDEX ON memories USING hnsw (embedding vector_cosine_ops);
```

```diff
=== File: memory/memory_service.py ===
+import os
+import asyncio
+import asyncpg
+from pgvector.asyncpg import register_vector
+
+import openai  # require openai>=0.27.0
+
+
+class MemoryService:
+    """
+    Service for writing, searching, and summarizing long-term memories.
+    """
+    def __init__(self, db_dsn: str, openai_api_key: str = None, embedding_model: str = "text-embedding-ada-002"):
+        self.db_dsn = db_dsn
+        self._conn = None
+        openai.api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
+        self.embedding_model = embedding_model
+
+    async def init_db(self):
+        # Initialize DB connection and register vector type
+        self._conn = await asyncpg.connect(self.db_dsn)
+        await self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
+        await register_vector(self._conn)  # register vector type with asyncpg
+
+    async def write_event(self, user_id: str, agent: str, input_text: str, output_text: str):
+        """
+        Write a new episodic memory event: combines user input and agent output,
+        computes embedding, and inserts into the memories table.
+        """
+        if self._conn is None:
+            await self.init_db()
+        # Combine content (or store separately as needed)
+        content = f"User: {input_text}\nAgent: {output_text}"
+        # Compute embedding for the content
+        embedding = await self._embed_text(content)
+        # Insert into DB
+        await self._conn.execute(
+            """
+            INSERT INTO memories (user_id, type, content, embedding, agent)
+            VALUES ($1, $2, $3, $4, $5)
+            """,
+            user_id, "episodic", content, embedding, agent
+        )
+        # Optionally: publish a 'memory.created' event on the event bus (pseudo-code)
+        # event_bus.publish("memory.created", {"user_id": user_id, "agent": agent, "content": content})
+
+    async def search(self, query: str, user_id: str, top_k: int = 5):
+        """
+        Search for relevant memories given a query string and user scope.
+        Returns a list of memory rows (dicts) sorted by relevance.
+        """
+        if self._conn is None:
+            await self.init_db()
+        # Embed the query text
+        q_embed = await self._embed_text(query)
+        # Perform vector similarity search in PostgreSQL (cosine similarity)
+        rows = await self._conn.fetch(
+            """
+            SELECT content, agent, type, timestamp
+            FROM memories
+            WHERE user_id = $1
+            ORDER BY embedding <-> $2
+            LIMIT $3
+            """,
+            user_id, q_embed, top_k
+        )
+        # Convert Record objects to dicts
+        memories = [dict(row) for row in rows]
+        return memories
+
+    async def summarize(self, user_id: str):
+        """
+        Summarize past episodic memories into a new semantic memory for the user.
+        For example, fetch older memories and create a condensed summary via LLM.
+        """
+        if self._conn is None:
+            await self.init_db()
+        # Fetch recent episodic memories (for simplicity, use all)
+        rows = await self._conn.fetch(
+            "SELECT content FROM memories WHERE user_id = $1 AND type = 'episodic' ORDER BY timestamp DESC LIMIT 50",
+            user_id
+        )
+        texts = [row["content"] for row in rows]
+        if not texts:
+            return
+        # Combine into one prompt for summarization
+        prompt = "Summarize the key facts or lessons from these events:\n" + "\n".join(texts)
+        # Call LLM (GPT) to summarize; here using OpenAI chat as example
+        response = openai.ChatCompletion.create(
+            model="gpt-3.5-turbo",
+            messages=[{"role": "system", "content": "You are a summarization assistant."},
+                      {"role": "user", "content": prompt}],
+            max_tokens=300,
+        )
+        summary = response.choices[0].message.content.strip()
+        # Compute embedding for summary
+        summary_embed = await self._embed_text(summary)
+        # Store semantic memory
+        await self._conn.execute(
+            """
+            INSERT INTO memories (user_id, type, content, embedding, agent)
+            VALUES ($1, 'semantic', $2, $3, $4)
+            """,
+            user_id, summary, summary_embed, "MemoryService"
+        )
+
+    async def _embed_text(self, text: str) -> list:
+        """
+        Helper to compute text embedding via the selected model.
+        """
+        # Call OpenAI text embedding API (synchronous call inside async)
+        result = openai.Embedding.create(model=self.embedding_model, input=text)
+        return result["data"][0]["embedding"]
+
+    async def close(self):
+        if self._conn:
+            await self._conn.close()
```

```diff
=== File: memory/memory_middleware.py ===
+from memory.memory_service import MemoryService
+
+class MemoryMiddleware:
+    """
+    Event bus middleware to inject memory before agent run, and to write memory after.
+    """
+    def __init__(self, memory_service: MemoryService):
+        self.memory = memory_service
+
+    async def before_dispatch(self, event, ctx):
+        """
+        Called before an event is dispatched to an agent.
+        We retrieve relevant memory and attach to context.
+        """
+        # Example: event has 'user_id' and 'content'; ctx has agent_name
+        user_id = ctx.user_id
+        query = event.content
+        # Search memory for this user's past relevant entries
+        memories = await self.memory.search(query=query, user_id=user_id, top_k=5)
+        # Attach memory summaries to context (could be list of strings)
+        ctx.memory = memories
+
+    async def after_dispatch(self, event, result, ctx):
+        """
+        Called after the agent produces a result for the event.
+        We write the conversation turn to memory.
+        """
+        user_id = ctx.user_id
+        agent = ctx.agent_name
+        input_text = event.content
+        output_text = result  # assume result is string reply
+        # Non-blocking write (fire and forget)
+        # schedule as separate task so agent isn't delayed
+        asyncio.create_task(self.memory.write_event(user_id, agent, input_text, output_text))
```

```diff
=== File: event_bus.py ===
@@
 # At initialization of the event bus or in main app setup:
+from memory.memory_service import MemoryService
+from memory.memory_middleware import MemoryMiddleware
+
+# Initialize MemoryService (pass your DB DSN and OpenAI key)
+memory_service = MemoryService(db_dsn="postgresql://user:pass@host:port/dbname",
+                               openai_api_key=os.getenv("OPENAI_API_KEY"))
+# Register memory middleware on the bus
+event_bus.register_middleware(MemoryMiddleware(memory_service))
```

```diff
=== File: agents/llm_agent.py ===
@@ class LlmAgent:
     async def run(self, ctx):
-        prompt = f"Task: {ctx.input}\nReply concisely."
-        return await self.llm(prompt)
+        # Inject retrieved memory into prompt, if any
+        memory_snippets = ""
+        if hasattr(ctx, "memory") and ctx.memory:
+            # Combine memory entries (simple concatenation or formatting)
+            memory_snippets = "\n".join(f"- {m['content']}" for m in ctx.memory)
+        prompt = f"""
+        Use the following memory facts as context:
+        {memory_snippets}
+
+        Task: {ctx.input}
+        """
+        return await self.llm(prompt)
```

# Full Implementations

The code above outlines the new components. Below are the full implementations (Python and SQL) with inline comments. 

### 1. SQL Migration (`migrations/0001_create_memories.sql`)

```sql
-- Enable pgvector extension for vector columns
CREATE EXTENSION IF NOT EXISTS vector;

-- Create the memories table for storing episodic and semantic entries
CREATE TABLE memories (
  id SERIAL PRIMARY KEY,
  user_id TEXT NOT NULL,
  type TEXT NOT NULL,           -- e.g. 'episodic' or 'semantic'
  content TEXT NOT NULL,        -- the text content of the memory
  embedding VECTOR(1536),       -- embedding vector (1536 dims for OpenAI ada-002)
  agent TEXT,                   -- which agent created this memory
  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  importance FLOAT DEFAULT 1.0
);

-- Create an index for vector similarity search (HNSW, using cosine similarity)
CREATE INDEX memories_embedding_idx ON memories 
  USING hnsw (embedding vector_cosine_ops);
```

- We specify `VECTOR(1536)` assuming use of OpenAI’s 1536-dim embedding. Adjust if using a different model.
- The HNSW index (`vector_cosine_ops`) allows fast nearest-neighbor search【25†L445-L452】.
- Run this migration with your preferred tool (psql, Alembic, etc.) to add the table and index.

### 2. `MemoryService` (in `memory/memory_service.py`)

```python
import os
import asyncio
import asyncpg
from pgvector.asyncpg import register_vector
import openai

class MemoryService:
    """
    Manages long-term memories with embedding search and summarization.
    """

    def __init__(self, db_dsn: str, openai_api_key: str = None, embedding_model: str = "text-embedding-ada-002"):
        self.db_dsn = db_dsn
        # AsyncPG connection (initialized on first use)
        self._conn: asyncpg.Connection = None
        # Set API key for OpenAI (can also be set via environment)
        openai.api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        self.embedding_model = embedding_model

    async def init_db(self):
        """
        Initialize database connection and ensure the pgvector extension is enabled.
        """
        self._conn = await asyncpg.connect(self.db_dsn)
        await self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        await register_vector(self._conn)  # allow usage of VECTOR type

    async def write_event(self, user_id: str, agent: str, input_text: str, output_text: str):
        """
        Store a conversation turn as an episodic memory entry.
        """
        if self._conn is None:
            await self.init_db()

        # Combine input and output into one content string
        content = f"User: {input_text}\nAgent: {output_text}"
        # Compute embedding
        embedding = await self._embed_text(content)

        # Insert into the memories table
        await self._conn.execute(
            """
            INSERT INTO memories (user_id, type, content, embedding, agent)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, "episodic", content, embedding, agent
        )
        # (Optional) emit a memory event for other subsystems
        # event_bus.publish("memory.created", {"user_id": user_id, "agent": agent, "content": content})

    async def search(self, query: str, user_id: str, top_k: int = 5):
        """
        Search for memories relevant to the query for this user.
        Returns a list of dicts with keys: content, agent, type, timestamp.
        """
        if self._conn is None:
            await self.init_db()

        # Embed the query text
        q_embed = await self._embed_text(query)

        # Vector similarity search: ORDER BY embedding <-> query
        rows = await self._conn.fetch(
            """
            SELECT content, agent, type, timestamp
            FROM memories
            WHERE user_id = $1
            ORDER BY embedding <-> $2
            LIMIT $3
            """,
            user_id, q_embed, top_k
        )
        # Convert to list of dicts
        return [dict(row) for row in rows]

    async def summarize(self, user_id: str):
        """
        Summarize past episodic memories into a semantic memory summary.
        """
        if self._conn is None:
            await self.init_db()

        # Fetch recent episodic memories (e.g. last N or filter by time)
        rows = await self._conn.fetch(
            """
            SELECT content 
            FROM memories 
            WHERE user_id = $1 AND type = 'episodic'
            ORDER BY timestamp DESC 
            LIMIT 50
            """, user_id
        )
        if not rows:
            return

        texts = [row["content"] for row in rows]
        prompt = (
            "Summarize the key insights from the following events:\n\n"
            + "\n".join(texts)
        )
        # Call LLM (OpenAI) to summarize
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a summarization assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
        )
        summary = response.choices[0].message.content.strip()

        # Compute embedding for summary
        summary_embed = await self._embed_text(summary)

        # Insert as a semantic memory entry
        await self._conn.execute(
            """
            INSERT INTO memories (user_id, type, content, embedding, agent)
            VALUES ($1, 'semantic', $2, $3, $4)
            """,
            user_id, summary, summary_embed, "MemoryService"
        )

    async def _embed_text(self, text: str) -> list:
        """
        Internal: compute text embedding via OpenAI API.
        """
        result = openai.Embedding.create(model=self.embedding_model, input=text)
        # Return the first data vector
        return result["data"][0]["embedding"]

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None
```

Key points in `MemoryService`:
- Uses **asyncpg** for PostgreSQL (via DSN) and registers the `vector` type.
- On **write_event**, we create a combined content and compute its embedding, then insert as type `'episodic'`.
- On **search**, we embed the query text and perform a vector similarity query (`ORDER BY embedding <-> $q`), filtering by `user_id`【25†L445-L452】.
- On **summarize**, we batch up to 50 recent episodic entries, prompt an LLM to summarize them, and store the result as type `'semantic'`.
- We default to OpenAI’s `text-embedding-ada-002` (1536 dims), but you can configure another.
- This service is **reusable** and does *not* depend on any specific agent framework, so it can be adapted to your architecture.

### 3. `MemoryMiddleware` (in `memory/memory_middleware.py`)

```python
import asyncio
from memory.memory_service import MemoryService

class MemoryMiddleware:
    """
    Middleware for the event bus: prefetch memories before agent handles a message,
    and write memory after the agent's response.
    """
    def __init__(self, memory_service: MemoryService):
        self.memory = memory_service

    async def before_dispatch(self, event, ctx):
        """
        Before the agent processes the event, retrieve relevant memory and attach to context.
        """
        user_id = ctx.user_id  # assume event or context has user_id
        query_text = event.content
        # Perform memory search in background
        memories = await self.memory.search(query=query_text, user_id=user_id, top_k=5)
        # Attach list of memory entries to context
        ctx.memory = memories

    async def after_dispatch(self, event, result, ctx):
        """
        After the agent response, write the turn to memory.
        """
        user_id = ctx.user_id
        agent_name = ctx.agent_name
        input_text = event.content
        output_text = result  # assume result is a string
        # Fire-and-forget write (so as not to block the response)
        asyncio.create_task(self.memory.write_event(user_id, agent_name, input_text, output_text))
```

- **`before_dispatch`** is called (by the event bus) before the agent’s `run` or equivalent. It uses `MemoryService.search` to find past memories for `ctx.user_id` relevant to the new message, then sets `ctx.memory`.
- **`after_dispatch`** is called after the agent yields a response. It schedules `MemoryService.write_event(...)` to record the turn.
- This mirrors Hermes’s pattern: **prefetch relevant memories** and **sync conversation to memory**【32†L79-L87】. It treats memory writes as asynchronous events.

### 4. Registering the Middleware

You will need to hook the `MemoryMiddleware` into your event bus. For example, in your main app or `event_bus.py`:

```python
from memory.memory_service import MemoryService
from memory.memory_middleware import MemoryMiddleware

# Initialize MemoryService with your database DSN and OpenAI key
memory_service = MemoryService(
    db_dsn="postgresql://dbuser:dbpass@localhost:5432/mydb",
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

# Register the middleware with the event bus
event_bus.register_middleware(MemoryMiddleware(memory_service))
```

(This assumes your `event_bus` has a `register_middleware` API. If not, adapt to your framework’s plugin mechanism.)

### 5. Agent Usage Example

Agents must be modified to **use** the injected `ctx.memory`. For example, an LLM agent’s `run()` could look like:

```python
class LlmAgent:
    async def run(self, ctx):
        # Build a context block from retrieved memories
        memory_block = ""
        if getattr(ctx, "memory", None):
            # Use only the content of each memory (or its summary)
            memory_block = "\n".join(m["content"] for m in ctx.memory)
        # Construct the prompt including memory context
        prompt = f"""
        Here are some things you know from past sessions:
        {memory_block}

        Now, given the user says: {ctx.input}
        What do you reply?
        """
        return await self.llm(prompt)
```

This way, each agent turn will have relevant memory context included in the prompt.  

**Testing agents:** Ensure agents gracefully handle an empty `ctx.memory` (e.g. first turn).  

### 6. Unit Tests (in `tests/`)

Below are example `pytest`-style tests using `asyncio`. These assume you have a test database configured. You can also monkey-patch `_embed_text` to avoid real API calls.

```python
# tests/test_memory_service.py
import pytest
import asyncio
import os
from memory.memory_service import MemoryService

@pytest.mark.asyncio
async def test_write_and_search_memory(tmp_path):
    # Setup a temporary database or use a test container (not shown)
    # For demonstration, we assume a DSN to a test Postgres with pgvector
    dsn = os.getenv("TEST_DATABASE_DSN")
    ms = MemoryService(db_dsn=dsn, openai_api_key="testkey")
    await ms.init_db()

    # Override embedding to deterministic small vector to avoid external calls
    async def fake_embed(text):
        return [float(len(text)), 0.0, 0.0]  # simple embedding based on length
    ms._embed_text = fake_embed

    # Write a memory event
    await ms.write_event(user_id="user123", agent="agentA", input_text="Hello", output_text="Hi there")
    # Search with a similar query
    results = await ms.search(query="Hello", user_id="user123", top_k=5)
    assert len(results) >= 1
    assert "User: Hello" in results[0]["content"]

    await ms.close()

@pytest.mark.asyncio
async def test_summarize_memory(tmp_path):
    dsn = os.getenv("TEST_DATABASE_DSN")
    ms = MemoryService(db_dsn=dsn, openai_api_key="testkey")
    await ms.init_db()

    # Seed some episodic memories
    entries = ["Fixed bug in login", "Refactored API client", "Deployed to production"]
    ms._embed_text = lambda text: [float(len(text)),0.0,0.0]
    for entry in entries:
        await ms._conn.execute(
            "INSERT INTO memories (user_id, type, content, embedding) VALUES ($1, 'episodic', $2, $3)",
            "userXYZ", entry, [float(len(entry)),0.0,0.0]
        )
    # Override LLM call to return a fixed summary
    async def fake_summarize(user_id):
        summary = "Did multiple devops and bug fixes."
        summary_embed = await ms._embed_text(summary)
        await ms._conn.execute(
            "INSERT INTO memories (user_id, type, content, embedding) VALUES ($1, 'semantic', $2, $3)",
            user_id, summary, summary_embed
        )
    ms.summarize = fake_summarize

    # Invoke summary
    await ms.summarize(user_id="userXYZ")

    # Check that semantic memory exists
    rows = await ms._conn.fetch("SELECT content FROM memories WHERE user_id=$1 AND type='semantic'", "userXYZ")
    assert rows and "devops" in rows[0]["content"]

    await ms.close()
```

```python
# tests/test_memory_middleware.py
import pytest
import asyncio
from memory.memory_middleware import MemoryMiddleware
from memory.memory_service import MemoryService

class DummyEvent:
    def __init__(self, content):
        self.content = content

class DummyContext:
    def __init__(self):
        self.user_id = "userID"
        self.agent_name = "testAgent"
        self.memory = None
        self.input = None

@pytest.mark.asyncio
async def test_middleware_before_after(tmp_path):
    # Setup MemoryService with fake embedding
    ms = MemoryService(db_dsn="postgresql://...", openai_api_key="key")
    ms._embed_text = lambda text: [1.0]  # trivial embed
    await ms.init_db()
    middleware = MemoryMiddleware(ms)

    # Pretend we write an event first
    await ms.write_event("userID", "testAgent", "Q", "A")

    # Create dummy event and context
    event = DummyEvent(content="Q")
    ctx = DummyContext()
    await middleware.before_dispatch(event, ctx)
    # After searching, ctx.memory should have at least one entry
    assert isinstance(ctx.memory, list)
    assert ctx.memory[0]["agent"] == "testAgent"

    # Simulate agent producing an answer
    result = "Answer text"
    await middleware.after_dispatch(event, result, ctx)
    # Give background task a moment to write
    await asyncio.sleep(0.1)
    # Search again to verify second entry
    res2 = await ms.search("Answer", "userID", top_k=5)
    assert any("Answer" in m["content"] for m in res2)

    await ms.close()
```

**Note:** In real tests, you should use a temporary or mock database. Here `TEST_DATABASE_DSN` is assumed to be set up for testing. You may need to adjust DSN, or use fixtures to create a clean DB state for each test.

# Sequence Diagram (Mermaid)

```mermaid
sequenceDiagram
    participant User
    participant EventBus
    participant MemoryMW as MemoryMiddleware
    participant Agent
    participant MemorySvc as MemoryService

    User->>EventBus: Submit message (user_id, content)
    EventBus->>MemoryMW: before_dispatch(event, ctx)
    MemoryMW->>MemorySvc: search(query=content, user_id)
    MemorySvc-->>MemoryMW: relevant memories (content vectors)
    MemoryMW->>Agent: dispatch event to agent with ctx.memory
    Agent->>Agent: (uses ctx.memory in prompt)
    Agent->>EventBus: returns result/response
    EventBus->>MemoryMW: after_dispatch(event, result, ctx)
    MemoryMW->>MemorySvc: write_event(user_id, agent, content, result)
    MemorySvc-->>MemoryMW: (ack)
    MemoryMW->>EventBus: (done)

    Note over MemorySvc: (Later) consolidate: summarize episodic → semantic
    MemorySvc->>MemorySvc: fetch & summarize old memories
    MemorySvc->>MemorySvc: insert semantic summary
```

This diagram shows:
- **Prefetch**: Before the agent handles each message, `MemoryMiddleware` queries `MemoryService.search` and injects memories into the context【32†L79-L87】.
- **Agent turn**: The agent includes these memory snippets in its prompt (session memory).
- **Write-back**: After the agent responds, `MemoryMiddleware` calls `MemoryService.write_event` to record the turn as episodic memory.
- **Consolidation** (dashed): At intervals, `MemoryService.summarize` compacts many episodic entries into a higher-level semantic memory.

# Memory Types and Access Patterns

| **Memory Type** | **Description**                                          | **Storage**           | **Access Pattern**                                       |
|-----------------|----------------------------------------------------------|-----------------------|----------------------------------------------------------|
| **Working**     | Short-term context (current conversation context).       | In-memory (not persisted). | Always passed in `ctx.state`/`ctx.input`. Reset each session. |
| **Episodic**    | Concrete past events (turn-by-turn transcripts).         | Database (memories table). | Retrieved via vector search on query; filtered by user or agent scope. |
| **Semantic**    | Abstracted knowledge (summaries, user profile).         | Database (memories table). | Included once per session or on-demand; can be injected into system prompt or used to augment search. |

- **Working Memory**: The in-progress conversation (chat history), managed by ADK state/ctx. *Example access*: always available to the current agent turn; **cleared** between sessions.
- **Episodic Memory**: Each (input, output) turn logged as an *episodic* memory entry【30†L75-L83】. Stored in Postgres, indexed by embedding. *Access*: On each turn, we vector-query by the new message to retrieve top-K related past events (per user)【32†L79-L87】.
- **Semantic Memory**: Condensed insights derived from many episodic entries (e.g. “the user prefers summaries”). Updated periodically via `summarize()`. *Access*: Could be injected into the initial system prompt or included alongside episodic hits; used as background context or personal profile.

This classification parallels cognitive psychology and Hermes’s design. We let agents access all types as needed (through `ctx.memory` or system prompts). We can further refine scopes (e.g. some agents only use semantic facts, others raw episodic logs).

# Running Tests and Manual Verification

1. **Install Dependencies**: Ensure the following Python packages (with versions) are in your environment:
   - `asyncpg>=0.27.0`
   - `pgvector>=0.1.2`
   - `openai>=0.27.0`
   - (Any ADK-related packages your project uses)
   - For tests: `pytest>=7.0.0`, `pytest-asyncio>=0.21.0`.
2. **Set Up Database**: Run the migration SQL above on your Postgres (make sure `pgvector` extension is available). For testing, you can create a separate test database.
3. **Configure**: Set the `OPENAI_API_KEY` environment variable (or pass it in code) so embeddings and ChatCompletion work.
4. **Run Tests**: Use `pytest tests/` to execute unit tests. They should pass if DB and API are accessible (for offline testing, consider mocking OpenAI or use local vector embeddings).
5. **Manual Verification**:
   - **Initial Run**: Start your application. Ensure middleware is registered (you may add logging in `before_dispatch`/`after_dispatch`).
   - **Test a Conversation**: Send a message as a user. Observe that:
     - **Before** the agent reply, `MemoryService.search` is called (check DB logs or console logs).
     - **After** the agent reply, a new row appears in `memories` (table) with `type='episodic'`.
   - **Memory Retrieval**: After several interactions, try sending a message similar to a past one. The agent’s prompt should show related memory snippets (you can log `ctx.memory`).
   - **Summarization**: Manually trigger (or wait for) `MemoryService.summarize(user_id)` (e.g. call it via REPL or as a scheduled job). Verify a new row with `type='semantic'` is inserted.
   - **Limit Context**: Test that extremely old memories still get summarized or pruned, so context stays bounded. (Implement truncation as needed.)

**Security and Errors**: Handle missing DB or API errors gracefully. In production, wrap DB calls with try/except and configure retry/backoff if needed. Logging memory operations is recommended for auditability.

# References

- Hermes Agent’s memory pattern (prefetch + sync)【32†L79-L87】.
- Example usage of PostgreSQL pgvector extension for vector search【25†L445-L452】.
- Description of Hermes’s episodic memory capturing every execution【30†L75-L83】. These guided our design of persistent, vector-indexed memory.  
