# my_evo_ai 项目文档

## 目录结构

```Plain Text

agents/
 └── lead_agent/
     ├── agent.py
     ├── runtime/
     │   ├── runner.py
     │   ├── event_bus.py
     │   ├── task_queue.py
     │   ├── a2a_router.py
     │   ├── skill_runtime.py
     │   └── sandbox.py
     ├── schemas/
     │   ├── event_schema.py
     │   ├── task_schema.py
     │   └── artifact_schema.py
     ├── sub_agents/
     │   ├── planner/agent.py
     │   ├── researcher/agent.py
     │   ├── coder/agent.py
     │   ├── reporter/agent.py
     │   └── skill_agent/agent.py
     ├── skills/
     │   ├── python_exec/
     │   │   ├── SKILL.md
     │   │   └── tool.py
     │   ├── web_search/
     │   │   ├── SKILL.md
     │   │   └── tool.py
     │   └── rag/
     │       ├── SKILL.md
     │       └── tool.py
```

## 1. lead agent

### agents/lead_agent/[agent.py](agent.py)

```Python

from google.adk.agents import LlmAgent
from google.adk.agents.workflow import SequentialAgent, ParallelAgent

from .sub_agents.planner.agent import planner_agent
from .sub_agents.researcher.agent import researcher_agent
from .sub_agents.coder.agent import coder_agent
from .sub_agents.reporter.agent import reporter_agent
from .sub_agents.skill_agent.agent import skill_agent


execution_stage = ParallelAgent(
    name="execution_stage",
    description="parallel task execution",
    sub_agents=[
        researcher_agent,
        coder_agent,
        skill_agent
    ]
)


lead_workflow = SequentialAgent(
    name="lead_workflow",
    description="plan → execute → synthesize",
    sub_agents=[
        planner_agent,
        execution_stage,
        reporter_agent
    ]
)


root_agent = LlmAgent(
    name="root_agent",
    model="gpt-4.1",
    description="top level harness agent",
    instruction="""
You are the root orchestration agent.
decide:
- direct response
- workflow execution
- skill usage
- A2A agent call
delegate complex tasks to lead_workflow
""",
    sub_agents=[
        lead_workflow
    ]
)
```

## 2. planner agent

### agents/lead_agent/sub_agents/planner/[agent.py](agent.py)

```Python

from google.adk.agents import LlmAgent


planner_agent = LlmAgent(
    name="planner",
    model="gpt-4.1",
    description="creates execution plan",
    instruction="""
break goal into structured plan
output json:
{
 "tasks":[
   {
     "id":"",
     "description":"",
     "agent":"researcher|coder|skill"
   }
 ]
}
"""
)
```

## 3. researcher agent

### agents/lead_agent/sub_agents/researcher/[agent.py](agent.py)

```Python

from google.adk.agents import LlmAgent


researcher_agent = LlmAgent(
    name="researcher",
    model="gpt-4.1",
    description="performs research",
    instruction="""
search information
summarize findings
use skill if needed
"""
)
```

## 4. coder agent

### agents/lead_agent/sub_agents/coder/[agent.py](agent.py)

```Python

from google.adk.agents import LlmAgent


coder_agent = LlmAgent(
    name="coder",
    model="gpt-4.1",
    description="writes and executes code",
    instruction="""
write python code
use python_exec skill when execution needed
"""
)
```

## 5. reporter agent

### agents/lead_agent/sub_agents/reporter/[agent.py](agent.py)

```Python

from google.adk.agents import LlmAgent


reporter_agent = LlmAgent(
    name="reporter",
    model="gpt-4.1",
    description="synthesizes result",
    instruction="""
combine outputs
produce final answer
"""
)
```

## 6. skill agent

### agents/lead_agent/sub_agents/skill_agent/[agent.py](agent.py)

```Python

from google.adk.agents import LlmAgent
from ...runtime.skill_runtime import skill_runtime


skill_agent = LlmAgent(
    name="skill_agent",
    model="gpt-4.1",
    description="executes skills",
    instruction="""
available skills:
python_exec
web_search
rag
select correct skill
""",
    tools=[
        skill_runtime.get_tool("python_exec"),
        skill_runtime.get_tool("web_search"),
        skill_runtime.get_tool("rag")
    ]
)
```

## 7. skill runtime

### agents/lead_agent/runtime/[skill_runtime.py](skill_runtime.py)

```Python

import os
import importlib.util


class SkillRuntime:
    def __init__(self):
        self.tools = {}

    def load_skill(self, skill_dir):
        tool_file = os.path.join(skill_dir, "tool.py")
        spec = importlib.util.spec_from_file_location(
            "tool",
            tool_file
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tool = module.get_tool()
        self.tools[tool.name] = tool

    def load_all(self, base_dir):
        for d in os.listdir(base_dir):
            path = os.path.join(base_dir, d)
            if os.path.isdir(path):
                self.load_skill(path)

    def get_tool(self, name):
        return self.tools[name]


skill_runtime = SkillRuntime()
skill_runtime.load_all(
    os.path.join(
        os.path.dirname(__file__),
        "../skills"
    )
)
```

## 8. python skill

### agents/lead_agent/skills/python_exec/[tool.py](tool.py)

```Python

from ...runtime.sandbox import ToolSandbox


sandbox = ToolSandbox()


class PythonExecTool:
    name = "python_exec"

    async def __call__(self, code:str):
        return sandbox.run_python(code)


def get_tool():
    return PythonExecTool()
```

### agents/lead_agent/skills/python_exec/[SKILL.md](SKILL.md)

```Plain Text

---
name: python_exec
description: execute python code in sandbox
---

use when code execution needed
```

## 9. web search skill

### agents/lead_agent/skills/web_search/[tool.py](tool.py)

```Python

import httpx


class WebSearchTool:
    name = "web_search"

    async def __call__(self, query:str):
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.duckduckgo.com",
                params={
                    "q":query,
                    "format":"json"
                }
            )
        return r.json()


def get_tool():
    return WebSearchTool()
```

### agents/lead_agent/skills/web_search/[SKILL.md](SKILL.md)

```Plain Text

---
name: web_search
description: web search information
---

use when need to search online information
```

## 10. rag skill

### agents/lead_agent/skills/rag/[tool.py](tool.py)

```Python

class RagTool:
    name = "rag"

    async def __call__(self, query:str):
        return {
            "result":"rag result placeholder"
        }


def get_tool():
    return RagTool()
```

### agents/lead_agent/skills/rag/[SKILL.md](SKILL.md)

```Plain Text

---
name: rag
description: retrieve augmented generation
---

use when need to query knowledge base
```

## 11. sandbox

### agents/lead_agent/runtime/[sandbox.py](sandbox.py)

```Python

import subprocess
import tempfile
import os


class ToolSandbox:
    def run_python(self, code:str):
        with tempfile.TemporaryDirectory() as tmp:
            file = os.path.join(tmp, "script.py")
            with open(file, "w") as f:
                f.write(code)
            result = subprocess.run(
                ["python", file],
                capture_output=True,
                text=True
            )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr
        }
```

## 12. A2A router

### agents/lead_agent/runtime/[a2a_router.py](a2a_router.py)

```Python

import httpx


class A2ARouter:
    def __init__(self):
        self.registry = {}

    def register(self, name, endpoint):
        self.registry[name] = endpoint

    async def call(self, name, payload):
        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.registry[name],
                json=payload
            )
        return r.json()
```

## 13. task queue

### agents/lead_agent/runtime/[task_queue.py](task_queue.py)

```Python

import asyncio


class TaskQueue:
    def __init__(self):
        self.queue = asyncio.Queue()

    async def add(self, task):
        await self.queue.put(task)

    async def worker(self):
        while True:
            task = await self.queue.get()
            await task()


task_queue = TaskQueue()
```

## 14. event bus

### agents/lead_agent/runtime/[event_bus.py](event_bus.py)

```Python

class EventBus:
    def __init__(self):
        self.listeners = []

    def subscribe(self, fn):
        self.listeners.append(fn)

    async def publish(self, event):
        for fn in self.listeners:
            await fn(event)


event_bus = EventBus()
```

## 15. runner

### agents/lead_agent/runtime/[runner.py](runner.py)

```Python

from google.adk.runners import Runner


class HarnessRunner:
    def __init__(
        self,
        root_agent,
        event_bus,
        task_queue
    ):
        self.runner = Runner(agent=root_agent)
        self.event_bus = event_bus
        self.task_queue = task_queue

    async def run(self, user_input:str):
        async for event in self.runner.run_async(user_input):
            await self.event_bus.publish(event)
            if event.type == "background_task":
                await self.task_queue.add(event)
```

## 16. event schema

### agents/lead_agent/schemas/[event_schema.py](event_schema.py)

```Python

from pydantic import BaseModel
from datetime import datetime


class AgentEvent(BaseModel):
    type:str
    agent:str
    task_id:str | None
    content:dict | str | None
    timestamp:datetime
```

## 17. task schema

### agents/lead_agent/schemas/[task_schema.py](task_schema.py)

```Python

from pydantic import BaseModel


class Task(BaseModel):
    id:str
    parent_id:str | None
    agent:str
    status:str
    input:dict
    output:dict | None
```

## 18. 使用方式

```Python

from agents.lead_agent.agent import root_agent
from agents.lead_agent.runtime.runner import HarnessRunner
from agents.lead_agent.runtime.event_bus import event_bus
from agents.lead_agent.runtime.task_queue import task_queue


runner = HarnessRunner(
    root_agent,
    event_bus,
    task_queue
)


async def chat(user_input):
    await runner.run(user_input)
```

## 最终效果

my_evo_ai 具备能力：

- Agent Kernel：root_agent

- Workflow orchestration：lead_workflow

- capability system：ADK skills

- safe execution：sandbox tools

- multi-agent communication：A2A router

- async runtime：background task queue

- streaming events：event bus
> （注：文档部分内容可能由 AI 生成）