# 生产级 Harness Agent Runtime 改造（v1.1）

# 生产级 Harness Agent Runtime 改造指南（v1.1）

将 my_evo_ai 改造为企业级多代理工作流运行时系统

版本更新说明：v1.1 针对生产分布式部署、多租户兼容、数据一致性、任务可靠性完成核心优化，完全对齐 my_evo_ai 原生架构

---

## 文档信息

|项目|内容|
|---|---|
|**目标仓库**|[https://github.com/RoyLLLL/my_evo_ai](https://github.com/RoyLLLL/my_evo_ai)|
|**参考架构**|Google ADK + Eigent Workforce Pattern|
|**预计工期**|10-14 天|
|**文档版本**|v1.1|
|**更新内容**|1. 复用项目原生SQLAlchemy Base模型，全异步DB操作<br>2. 基于Redis Pub/Sub实现分布式事件总线<br>3. 对齐原生多租户体系，补充org_id/client_id字段<br>4. 新增Redis分布式锁，防止任务重复执行<br>5. 新增Celery死信队列(DLQ)，保障异常任务不丢失|
---

## 目录

1. [概述](#1-概述)

2. [准备阶段](#2-准备阶段)

3. [核心架构植入](#3-核心架构植入)

4. [系统集成](#4-系统集成)

5. [生产级加固](#5-生产级加固)

6. [部署与运维](#6-部署与运维)

7. [附录](#7-附录)

---

## 1. 概述

### 1.1 改造目标

将 `my_evo_ai` 从原型项目转变为**生产级 Harness Agent Runtime**，支持：

- ✅ 多代理智能编排（Router → Lead → Sub-agents）

- ✅ 长时任务执行与状态恢复

- ✅ 事件驱动的实时前后端通信

- ✅ ADK Skills 能力热加载

- ✅ 集群环境高可用与分布式部署

- ✅ 完全对齐原生多租户权限体系

- ✅ 企业级可观测性与安全

### 1.2 核心设计理念

借鉴现代 Agent Runtime 架构（Devin、OpenHands、Eigent），采用分层设计，100%兼容my_evo_ai原生技术栈：

```Plain Text

┌─────────────────────────────────────────────────────────────┐
│                     Frontend (WebSocket UI)                  │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                  Chat Router API / WebSocket                 │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                    Router Agent (Lightweight)                │
│          ┌───────────────┴───────────────┐                   │
│          ▼                               ▼                   │
│    Simple Agent                    Lead Agent                │
│          │                    ┌────────┴────────┐           │
│          │                    ▼                 ▼           │
│          │              Task Agent      Domain Agents       │
│          │                    │                 │           │
│          └────────────────────┴─────────────────┘           │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  Tools Layer: LongRunningTool / SkillToolset / Custom      │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│      Task Runtime + Distributed Event Bus + Task Queue      │
│  ┌──────────────────┐  ┌──────────────────────────┐        │
│  │ Task Persistence │  │  WebSocket Streaming     │        │
│  │  (PostgreSQL)    │  │  (Status/Progress/Artifacts)│    │
│  └──────────────────┘  └──────────────────────────┘        │
│  ┌──────────────────────────────────────────────────┐        │
│  │  Distributed Lock + DLQ Dead Letter Queue       │        │
│  └──────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 准备阶段（第 1-2 天）

### 2.1 环境搭建

```Bash

# 1. 克隆代码库
git clone https://github.com/RoyLLLL/my_evo_ai
cd my_evo_ai

# 2. 安装基础依赖
pip install -r requirements.txt

# 3. 安装 ADK 及生产级依赖
pip install google-adk sqlalchemy[asyncio] asyncpg redis celery structlog prometheus-client python-multipart python-magic

# 4. 验证现有流程
# 确保 run_agent_stream 能正常运行，确认项目原生Base模型、Redis配置、多租户字段位置
```

### 2.2 代码库梳理

重点理解以下核心模块，为对齐原生架构做准备：

- `src/database/base.py`：项目原生SQLAlchemy `Base` 模型（必复用，避免多Base冲突）

- `src/services/database_session_service.py`：原生异步DB会话管理

- `src/models/`：原生多租户模型（`org_id`/`client_id`字段定义）

- `src/core/config.py`：原生Redis连接配置

- `AgentBuilder`：代理构建逻辑

- `run_agent_stream`：现有流式输出函数

### 2.3 参考资料阅读

- Google ADK Samples: `https://github.com/google/adk-python/tree/main/contributing/samples`

- ADK Long-Run Tool: `https://google.github.io/adk-docs/tools-custom/function-tools/#long-run-tool`

- Eigent 架构思想: `https://github.com/eigent-ai/eigent`

---

## 3. 核心架构植入（第 3-7 天）

### 3.1 目录结构创建

```Bash

mkdir -p src/services/harness/{agents,runtime,tools,events,models,skills/example_skill,references,config}
```

最终结构（完全对齐项目原生src目录规范）：

```Plain Text

my_evo_ai/
├── src/
│   ├── services/
│   │   ├── harness/           # 新增：Harness 核心层
│   │   │   ├── agents/
│   │   │   │   ├── router_agent.py
│   │   │   │   ├── lead_agent.py
│   │   │   │   ├── simple_agent.py
│   │   │   │   ├── task_agent.py
│   │   │   │   └── registry.py
│   │   │   ├── runtime/
│   │   │   │   ├── runner.py
│   │   │   │   ├── task_runtime.py
│   │   │   │   ├── session_manager.py
│   │   │   │   ├── event_stream.py
│   │   │   │   ├── celery_app.py
│   │   │   │   └── celery_tasks.py
│   │   │   ├── tools/
│   │   │   │   ├── long_running_tool.py
│   │   │   │   ├── task_query_tool.py
│   │   │   │   └── skill_loader.py
│   │   │   ├── skills/
│   │   │   │   └── example_skill/
│   │   │   ├── models/
│   │   │   │   └── task_model.py
│   │   │   ├── events/
│   │   │   │   ├── event_types.py
│   │   │   │   ├── event_serializer.py
│   │   │   │   └── event_bus.py
│   │   │   └── config/
│   │   │       ├── logging.py
│   │   │       ├── metrics.py
│   │   │       └── lock.py
│   │   └── super_agent/        # 保留：项目原生实现
│   ├── api/
│   │   └── websocket.py         # 修改：集成 Harness
│   ├── database/                # 复用：项目原生Base模型
│   ├── models/                  # 复用：项目原生多租户模型
│   └── core/                    # 复用：项目原生配置、加密、Redis连接
├── migrations/                  # 复用：项目原生Alembic迁移体系
├── frontend/                    # 保留：项目原生前端
└── 其他原生项目文件
```

### 3.2 数据模型层（已优化：多租户对齐+原生Base复用+全异步操作）

#### 3.2.1 任务表模型 (`src/services/harness/models/task_model.py`)

优化点：复用项目原生Base模型、补充多租户org_id/client_id字段、完善索引

```Python

import uuid
from sqlalchemy import Column, String, DateTime, JSON, Float, Index
from datetime import datetime
# 关键优化：复用项目原生SQLAlchemy Base模型，避免多Base冲突
from src.database.base import Base


class Task(Base):
    __tablename__ = "harness_tasks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # 优化：对齐原生多租户体系，补充核心租户字段
    org_id = Column(String, index=True, nullable=False, comment="组织ID")
    client_id = Column(String, index=True, nullable=False, comment="客户端ID")
    user_id = Column(String, index=True, nullable=False, comment="用户ID")
    
    agent_name = Column(String)
    status = Column(String, index=True)  # pending/running/completed/failed/cancelled
    input = Column(JSON)
    output = Column(JSON)
    progress = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resumable_state = Column(JSON, comment="任务断点续跑检查点数据")

    __table_args__ = (
        # 优化：多租户复合索引，适配原生权限查询场景
        Index('idx_org_client_user_status', 'org_id', 'client_id', 'user_id', 'status'),
        Index('idx_status_created_at', 'status', 'created_at'),
    )
```

#### 3.2.2 数据库迁移脚本

创建 `migrations/versions/add_harness_tasks_table.py`（完全对齐项目原生Alembic规范）：

```Python

"""add harness_tasks table

Revision ID: add_harness_tasks_001
Revises: 【填写项目上一个迁移版本号】
Create Date: 2026-03-21

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_harness_tasks_001'
down_revision = '【填写项目上一个迁移版本号】'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'harness_tasks',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('client_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('agent_name', sa.String()),
        sa.Column('status', sa.String()),
        sa.Column('input', sa.JSON()),
        sa.Column('output', sa.JSON()),
        sa.Column('progress', sa.Float(), default=0.0),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('updated_at', sa.DateTime()),
        sa.Column('resumable_state', sa.JSON()),
    )
    # 创建复合索引，对齐多租户查询场景
    op.create_index('idx_org_client_user_status', 'harness_tasks', ['org_id', 'client_id', 'user_id', 'status'])
    op.create_index('idx_status_created_at', 'harness_tasks', ['status', 'created_at'])


def downgrade():
    op.drop_index('idx_org_client_user_status', 'harness_tasks')
    op.drop_index('idx_status_created_at', 'harness_tasks')
    op.drop_table('harness_tasks')
```

执行迁移（使用项目原生命令）：

```Bash

make alembic-upgrade
```

### 3.3 事件驱动层（已优化：Redis Pub/Sub分布式事件总线）

#### 3.3.1 事件类型定义 (`src/services/harness/events/event_types.py`)

```Python

from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class EventType(str, Enum):
    MESSAGE = "message"
    TOKEN = "token"
    ARTIFACT = "artifact"
    TASK_STARTED = "task_started"
    TASK_UPDATE = "task_update"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    AGENT_STATUS = "agent_status"
    SYSTEM = "system"


class Artifact(BaseModel):
    type: str = Field(..., description="File type: pdf, html, json, image")
    url: Optional[str] = None
    data: Optional[str] = None  # base64, for small files


class HarnessEvent(BaseModel):
    type: EventType
    org_id: Optional[str] = None
    client_id: Optional[str] = None
    agent: Optional[str] = None
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    user_id: Optional[str] = None
    content: Optional[str] = None
    progress: Optional[float] = Field(None, ge=0.0, le=1.0)
    artifact: Optional[Artifact] = None
    metadata: Optional[Dict[str, Any]] = None
    timestamp: float = Field(default_factory=lambda: datetime.utcnow().timestamp())
```

#### 3.3.2 分布式事件总线 (`src/services/harness/events/event_bus.py`)

优化点：基于项目原生Redis栈实现分布式事件总线，支持多副本跨节点事件推送，完全兼容原有接口

```Python

import asyncio
import json
import logging
from typing import Dict, Set, Optional
from collections import defaultdict
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
# 复用项目原生Redis连接配置
from src.core.config import settings

logger = logging.getLogger(__name__)

# 事件总线Redis频道前缀
EVENT_BUS_CHANNEL_PREFIX = "harness:event_bus:"


class EventBus:
    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        # 本地订阅者：{user_id: Set[asyncio.Queue]}
        self._local_subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()
        # 全局PubSub实例
        self._pubsub: Optional[PubSub] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._initialized = False

    async def initialize(self):
        """初始化事件总线，启动全局频道监听（服务启动时执行一次）"""
        if self._initialized:
            return
        
        self._pubsub = self.redis.pubsub()
        # 监听全局事件频道
        await self._pubsub.psubscribe(f"{EVENT_BUS_CHANNEL_PREFIX}*")
        # 启动后台监听任务
        self._listen_task = asyncio.create_task(self._listen_global_events())
        self._initialized = True
        logger.info("Distributed EventBus initialized successfully")

    async def _listen_global_events(self):
        """监听Redis全局频道的事件，分发到本地订阅者"""
        if not self._pubsub:
            return
        
        try:
            async for message in self._pubsub.listen():
                if message["type"] != "pmessage":
                    continue
                
                # 解析频道和事件
                channel = message["channel"].decode()
                user_id = channel.replace(EVENT_BUS_CHANNEL_PREFIX, "")
                try:
                    event = json.loads(message["data"].decode())
                except Exception as e:
                    logger.error(f"Failed to parse event from Redis: {e}")
                    continue
                
                # 分发到本地订阅者
                async with self._lock:
                    if user_id not in self._local_subscribers:
                        continue
                    queues = list(self._local_subscribers[user_id])
                
                for queue in queues:
                    try:
                        await queue.put(event)
                    except asyncio.QueueFull:
                        logger.warning(f"Event queue full for user {user_id}, dropping event")
                    except Exception as e:
                        logger.error(f"Failed to dispatch event to local queue: {e}")
        except Exception as e:
            logger.error(f"Global event listener failed: {e}", exc_info=True)

    async def publish(self, user_id: str, event: dict):
        """向指定用户的所有订阅者发布事件（跨节点生效）"""
        try:
            # 序列化事件
            event_json = json.dumps(event)
            # 发布到Redis全局频道
            channel = f"{EVENT_BUS_CHANNEL_PREFIX}{user_id}"
            await self.redis.publish(channel, event_json)
        except Exception as e:
            logger.error(f"Failed to publish event to Redis: {e}")

    async def subscribe(self, user_id: str) -> asyncio.Queue:
        """订阅用户事件，返回本地队列"""
        queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._local_subscribers[user_id].add(queue)
        logger.debug(f"User {user_id} subscribed to event bus, current subscribers: {len(self._local_subscribers[user_id])}")
        return queue

    async def unsubscribe(self, user_id: str, queue: asyncio.Queue):
        """取消订阅"""
        async with self._lock:
            if user_id in self._local_subscribers:
                self._local_subscribers[user_id].discard(queue)
                if not self._local_subscribers[user_id]:
                    del self._local_subscribers[user_id]
        logger.debug(f"User {user_id} unsubscribed from event bus")

    async def close(self):
        """关闭事件总线（服务停止时执行）"""
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        
        if self._pubsub:
            await self._pubsub.close()
        
        self._initialized = False
        logger.info("EventBus closed successfully")


# 全局实例初始化（在FastAPI启动事件中执行initialize）
def get_event_bus() -> EventBus:
    redis_client = Redis.from_url(settings.REDIS_URL)
    return EventBus(redis_client)

event_bus = get_event_bus()
```

#### 3.3.3 事件序列化器 (`src/services/harness/events/event_serializer.py`)

```Python

import json
import logging
from datetime import datetime
from google.adk.events import Event as AdkEvent
from src.services.harness.events.event_types import HarnessEvent, EventType, Artifact

logger = logging.getLogger(__name__)

def serialize_event(adk_event: AdkEvent) -> str:
    """
    将 ADK 原生事件包装为 Harness 事件
    兼容现有 run_agent_stream 的输出格式
    """
    try:
        event_dict = adk_event.dict()
        harness_event = HarnessEvent(type=EventType.SYSTEM)

        if "content" in event_dict and event_dict["content"]:
            content = event_dict["content"]
            if "parts" in content:
                for part in content["parts"]:
                    if isinstance(part, dict):
                        # 处理文本
                        if "text" in part and part["text"]:
                            harness_event = HarnessEvent(
                                type=EventType.MESSAGE,
                                agent=event_dict.get("author"),
                                content=part["text"]
                            )
                        # 处理 Artifact (inline_data)
                        elif "inline_data" in part:
                            artifact = Artifact(
                                type=part["inline_data"].get("mime_type", "application/octet-stream"),
                                data=part["inline_data"].get("data")
                            )
                            harness_event = HarnessEvent(
                                type=EventType.ARTIFACT,
                                agent=event_dict.get("author"),
                                artifact=artifact
                            )
        
        # 处理工具调用
        if "tool_calls" in event_dict and event_dict["tool_calls"]:
            harness_event = HarnessEvent(
                type=EventType.SYSTEM,
                metadata={"tool_calls": event_dict["tool_calls"]}
            )

        return harness_event.json()
    
    except Exception as e:
        logger.error(f"Error serializing ADK event: {e}")
        return HarnessEvent(
            type=EventType.SYSTEM,
            content=f"Error: {str(e)}"
        ).json()

def serialize_harness_event(event: dict) -> str:
    """直接序列化 Harness 事件字典"""
    try:
        # 确保 type 是字符串
        if "type" in event and isinstance(event["type"], EventType):
            event["type"] = event["type"].value
        
        # 添加时间戳
        if "timestamp" not in event:
            event["timestamp"] = datetime.utcnow().timestamp()
            
        return json.dumps(event)
    except Exception as e:
        logger.error(f"Error serializing harness event: {e}")
        return json.dumps({"type": "system", "content": f"Error: {str(e)}"})
```

### 3.4 代理层（无核心变更，仅补充多租户参数透传）

#### 3.4.1 路由代理 (`src/services/harness/agents/router_agent.py`)

```Python

import logging
from typing import Optional
from google.adk.agents import Agent

logger = logging.getLogger(__name__)

class RouterAgent:
    """轻量级路由代理，不依赖 LLM，快速决策"""
    
    @staticmethod
    def route(text: str) -> str:
        """
        路由决策逻辑
        返回: 'simple_agent' 或 'lead_agent'
        """
        text_lower = text.lower()
        
        # 简单问题关键词
        simple_keywords = ["hello", "hi", "hey", "what is", "how to", "help", "?"]
        is_simple = len(text) < 50 and any(k in text_lower for k in simple_keywords)
        
        # 复杂任务关键词
        complex_keywords = ["generate", "create", "build", "analyze", "report", "task", "process"]
        is_complex = any(k in text_lower for k in complex_keywords)
        
        if is_complex:
            return "lead_agent"
        if is_simple:
            return "simple_agent"
            
        # 默认路由到 lead_agent
        return "lead_agent"

# 可选：基于 LLM 的智能路由代理
llm_router_agent = Agent(
    name="llm_router_agent",
    model="gemini-2.0-flash",
    instruction="""
You are a router agent. Decide which agent should handle the user request.
Respond with ONLY ONE WORD: either "simple_agent" or "lead_agent".

- Use "simple_agent" for: greetings, simple questions, FAQs, short queries
- Use "lead_agent" for: complex tasks, workflows, generation, analysis, multi-step requests
""",
)
```

#### 3.4.2 主协调代理 (`src/services/harness/agents/lead_agent.py`)

```Python

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from src.services.harness.agents.simple_agent import simple_agent
from src.services.harness.agents.task_agent import task_agent
from src.services.harness.tools.skill_loader import load_skills

# 使用 Agent-as-Tool 包装子代理
simple_agent_tool = AgentTool(
    agent=simple_agent, 
    skip_summarization=True,
    name="simple_agent_tool",
    description="Delegate simple questions and FAQs to this agent."
)

task_agent_tool = AgentTool(
    agent=task_agent, 
    skip_summarization=True,
    name="task_agent_tool",
    description="Delegate complex, long-running tasks and workflows to this agent."
)

lead_agent = LlmAgent(
    name="lead_agent",
    model="gemini-2.5-pro",
    instruction="""
You are the lead coordinator agent. Your role is to orchestrate the workflow.

1. For simple questions, greetings, or FAQs: use simple_agent_tool
2. For complex tasks, generation, analysis, or long-running work: use task_agent_tool
3. After receiving the result from a sub-agent, summarize it clearly for the user.

Always use the appropriate tool instead of answering directly.
""",
    tools=[simple_agent_tool, task_agent_tool, load_skills()],
)
```

#### 3.4.3 简单问题代理 (`src/services/harness/agents/simple_agent.py`)

```Python

from google.adk.agents import LlmAgent

simple_agent = LlmAgent(
    name="simple_agent",
    model="gemini-2.5-flash",
    instruction="""
You are a helpful assistant for simple questions and FAQs.
- Answer clearly and concisely
- Be friendly and professional
- If the question is complex, suggest the user ask for more advanced help
""",
)
```

#### 3.4.4 长时任务代理 (`src/services/harness/agents/task_agent.py`)

```Python

from google.adk.agents import LlmAgent
from src.services.harness.tools.long_running_tool import long_task_tool

task_agent = LlmAgent(
    name="task_agent",
    model="gemini-2.5-pro",
    instruction="""
You handle complex, long-running workflows and tasks.

When given a task:
1. Understand the requirements clearly
2. Use the start_long_task tool to execute the work asynchronously
3. Report the task_id to the user so they can track progress
4. When the task completes, summarize the results

Always use the start_long_task tool for any non-trivial work.
""",
    tools=[long_task_tool],
)
```

### 3.5 工具层

#### 3.5.1 长时任务工具 (`src/services/harness/tools/long_running_tool.py`)

优化：补充多租户参数透传

```Python

import logging
from google.adk.tools import LongRunningFunctionTool
from src.services.harness.runtime.task_runtime import get_task_runtime

logger = logging.getLogger(__name__)

def start_task(input: dict) -> dict:
    """
    启动长时任务并立即返回 Ticket ID
    符合 ADK LongRunningFunctionTool 模式
    """
    try:
        runtime = get_task_runtime()
        task = runtime.create_task(
            agent_name="task_agent",
            org_id=input.get("org_id", "unknown"),
            client_id=input.get("client_id", "unknown"),
            user_id=input.get("user_id", "unknown"),
            input=input
        )
        
        logger.info(f"Started long-running task: {task.id}")
        
        return {
            "task_id": task.id,
            "status": "started",
            "message": "Task initiated successfully. Progress will be streamed in real-time."
        }
    except Exception as e:
        logger.error(f"Failed to start task: {e}")
        return {
            "status": "error",
            "message": f"Failed to start task: {str(e)}"
        }

long_task_tool = LongRunningFunctionTool(
    func=start_task,
    name="start_long_task",
    description="""
    Starts a long-running asynchronous task. 
    Pass the full task description, org_id, client_id, user_id in the input.
    Returns a task_id immediately for progress tracking.
    """
)
```

#### 3.5.2 Skills 加载器 (`src/services/harness/tools/skill_loader.py`)

```Python

import pathlib
import logging
from typing import Optional
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset

logger = logging.getLogger(__name__)

# 全局缓存
_skill_toolset: Optional[skill_toolset.SkillToolset] = None

def load_skills():
    """
    从目录加载 Skills 并返回 SkillToolset
    使用单例模式避免重复加载
    """
    global _skill_toolset
    
    if _skill_toolset is not None:
        return _skill_toolset
    
    try:
        skill_dir = pathlib.Path(__file__).parent.parent / "skills" / "example_skill"
        
        if not skill_dir.exists():
            logger.warning(f"Skill directory not found: {skill_dir}")
            # 返回空的 toolset
            _skill_toolset = skill_toolset.SkillToolset(skills=[])
            return _skill_toolset
        
        logger.info(f"Loading skills from: {skill_dir}")
        skill = load_skill_from_dir(skill_dir)
        _skill_toolset = skill_toolset.SkillToolset(skills=[skill])
        
        logger.info(f"Successfully loaded skill: {skill.name}")
        return _skill_toolset
        
    except Exception as e:
        logger.error(f"Failed to load skills: {e}")
        _skill_toolset = skill_toolset.SkillToolset(skills=[])
        return _skill_toolset

def reload_skills():
    """强制重新加载 Skills"""
    global _skill_toolset
    _skill_toolset = None
    return load_skills()
```

### 3.6 运行时层（已优化：全异步DB操作+分布式锁防重复执行）

#### 3.6.1 分布式锁配置 (`src/services/harness/config/lock.py`)

```Python

import asyncio
import logging
from redis.asyncio import Redis
from src.core.config import settings

logger = logging.getLogger(__name__)

# 锁默认超时时间：30秒，避免死锁
DEFAULT_LOCK_TIMEOUT = 30
# 锁前缀
LOCK_PREFIX = "harness:lock:"


class DistributedLock:
    """基于Redis的分布式锁，防止多节点任务重复执行"""
    def __init__(self, redis_client: Redis):
        self.redis = redis_client

    async def acquire(self, lock_key: str, timeout: int = DEFAULT_LOCK_TIMEOUT) -> bool:
        """获取锁，成功返回True，失败返回False"""
        full_key = f"{LOCK_PREFIX}{lock_key}"
        # 使用SETNX+EX原子操作获取锁
        result = await self.redis.set(full_key, "1", nx=True, ex=timeout)
        return result is not None

    async def release(self, lock_key: str):
        """释放锁"""
        full_key = f"{LOCK_PREFIX}{lock_key}"
        await self.redis.delete(full_key)

    async def execute_with_lock(self, lock_key: str, func, *args, timeout: int = DEFAULT_LOCK_TIMEOUT, **kwargs):
        """带锁执行函数，确保同一时间只有一个节点执行"""
        if not await self.acquire(lock_key, timeout):
            logger.warning(f"Failed to acquire lock for key: {lock_key}, task already running on another node")
            return None
        
        try:
            return await func(*args, **kwargs)
        finally:
            await self.release(lock_key)


# 全局锁实例
def get_distributed_lock() -> DistributedLock:
    redis_client = Redis.from_url(settings.REDIS_URL)
    return DistributedLock(redis_client)

distributed_lock = get_distributed_lock()
```

#### 3.6.2 任务运行时引擎 (`src/services/harness/runtime/task_runtime.py`)

优化点：全异步SQLAlchemy操作、分布式锁防重复执行、多租户权限校验

```Python

import asyncio
import logging
import uuid
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from src.services.harness.models.task_model import Task
from src.services.harness.events.event_bus import event_bus
from src.services.harness.config.lock import distributed_lock

logger = logging.getLogger(__name__)

# 全局实例
_task_runtime: Optional['TaskRuntime'] = None

def get_task_runtime() -> 'TaskRuntime':
    if _task_runtime is None:
        raise RuntimeError("TaskRuntime not initialized. Call init_task_runtime() first.")
    return _task_runtime

def init_task_runtime(db: AsyncSession):
    global _task_runtime
    _task_runtime = TaskRuntime(db)
    return _task_runtime

class TaskRuntime:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def create_task(self, agent_name: str, org_id: str, client_id: str, user_id: str, input: dict) -> Task:
        """创建新任务并启动异步执行（全异步操作）"""
        task = Task(
            id=str(uuid.uuid4()),
            org_id=org_id,
            client_id=client_id,
            user_id=user_id,
            agent_name=agent_name,
            status="pending",
            input=input,
            progress=0.0
        )
        
        # 优化：全异步DB操作，避免同步阻塞
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        
        # 启动异步执行
        asyncio.create_task(self._execute_task(task.id))
        return task

    async def _execute_task(self, task_id: str):
        """执行任务的核心逻辑（全异步操作+分布式锁防重复执行）"""
        # 先获取分布式锁，确保同一任务只有一个节点执行
        lock_key = f"task_execution:{task_id}"
        if not await distributed_lock.acquire(lock_key, timeout=3600):  # 任务最长执行1小时
            logger.warning(f"Task {task_id} is already running on another node, skip execution")
            return

        try:
            task = await self._get_task(task_id)
            if not task:
                logger.error(f"Task {task_id} not found")
                return

            # 优化：全异步状态更新
            task.status = "running"
            await self._save_task(task)
            
            # 推送任务开始事件
            await event_bus.publish(task.user_id, {
                "type": "task_started",
                "task_id": task_id,
                "org_id": task.org_id,
                "client_id": task.client_id,
                "agent": task.agent_name
            })

            # --- 示例任务逻辑：替换为实际工作流 ---
            # 实际项目中，这里应该调用具体的 Agent 或工作流
            total_steps = 10
            for step in range(total_steps):
                # 检查是否被取消
                if task_id not in self.running_tasks:
                    logger.info(f"Task {task_id} was cancelled")
                    return
                
                # 模拟工作
                await asyncio.sleep(1)
                
                # 更新进度
                task.progress = (step + 1) / total_steps
                task.resumable_state = {"current_step": step}
                await self._save_task(task)
                
                # 推送进度更新
                await event_bus.publish(task.user_id, {
                    "type": "task_update",
                    "task_id": task_id,
                    "progress": task.progress,
                    "message": f"Processing step {step + 1}/{total_steps}"
                })
            # ----------------------------------------

            # 任务完成
            task.status = "completed"
            task.output = {
                "result": "success",
                "data": "Task completed successfully",
                "completed_at": datetime.utcnow().isoformat()
            }
            await self._save_task(task)
            
            await event_bus.publish(task.user_id, {
                "type": "task_completed",
                "task_id": task_id,
                "output": task.output
            })
            
            logger.info(f"Task {task_id} completed successfully")

        except asyncio.CancelledError:
            task = await self._get_task(task_id)
            if task:
                task.status = "cancelled"
                await self._save_task(task)
                await event_bus.publish(task.user_id, {
                    "type": "task_cancelled",
                    "task_id": task_id
                })
            logger.info(f"Task {task_id} was cancelled")
            
        except Exception as e:
            logger.error(f"Task {task_id} failed: {str(e)}", exc_info=True)
            task = await self._get_task(task_id)
            if task:
                task.status = "failed"
                task.output = {"error": str(e)}
                await self._save_task(task)
                
                await event_bus.publish(task.user_id, {
                    "type": "task_failed",
                    "task_id": task_id,
                    "error": str(e)
                })
            
        finally:
            # 释放分布式锁
            await distributed_lock.release(lock_key)
            # 清理本地任务
            async with self._lock:
                self.running_tasks.pop(task_id, None)

    async def cancel_task(self, task_id: str, org_id: str, client_id: str, user_id: str) -> bool:
        """取消正在运行的任务（带多租户权限校验）"""
        task = await self._get_task(task_id)
        # 优化：多租户权限校验，避免越权访问
        if not task or task.org_id != org_id or task.client_id != client_id or task.user_id != user_id:
            return False
            
        if task.status not in ["pending", "running"]:
            return False
            
        async with self._lock:
            if task_id in self.running_tasks:
                self.running_tasks[task_id].cancel()
                return True
        
        # 如果任务还没开始运行，直接标记为 cancelled
        task.status = "cancelled"
        await self._save_task(task)
        return True

    async def get_task_status(self, task_id: str, org_id: str, client_id: str, user_id: str) -> Optional[Task]:
        """获取任务状态（带多租户权限校验）"""
        task = await self._get_task(task_id)
        # 优化：多租户权限校验
        if task and task.org_id == org_id and task.client_id == client_id and task.user_id == user_id:
            return task
        return None

    async def list_user_tasks(self, org_id: str, client_id: str, user_id: str, status: Optional[str] = None, limit: int = 50) -> list[Task]:
        """列出用户的任务（带多租户过滤）"""
        query = select(Task).where(
            Task.org_id == org_id,
            Task.client_id == client_id,
            Task.user_id == user_id
        )
        if status:
            query = query.where(Task.status == status)
        query = query.order_by(Task.created_at.desc()).limit(limit)
        
        result = await self.db.execute(query)
        return list(result.scalars())

    async def resume_pending_tasks(self):
        """集群启动时恢复 running 状态的任务（带分布式锁，避免多节点重复恢复）"""
        # 全局锁，确保只有一个节点执行任务恢复
        await distributed_lock.execute_with_lock(
            lock_key="resume_pending_tasks",
            func=self._do_resume_pending_tasks,
            timeout=60
        )

    async def _do_resume_pending_tasks(self):
        """实际执行任务恢复的逻辑"""
        logger.info("Resuming pending tasks...")
        
        query = select(Task).where(Task.status == "running")
        result = await self.db.execute(query)
        tasks = list(result.scalars())
        
        for task in tasks:
            logger.info(f"Resuming task: {task.id}")
            # 标记为需要重试
            task.status = "pending"
            await self._save_task(task)
            asyncio.create_task(self._execute_task(task.id))
            
        logger.info(f"Resumed {len(tasks)} tasks")

    # --- 辅助方法（全异步）---
    async def _get_task(self, task_id: str) -> Optional[Task]:
        return await self.db.get(Task, task_id)
        
    async def _save_task(self, task: Task):
        task.updated_at = datetime.utcnow()
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
```

---

## 4. 系统集成（第 8-9 天）

### 4.1 修改现有 `run_agent_stream`

找到现有的流式函数（通常在 `src/services/` 下），进行如下修改（补充多租户参数透传）：

```Python

# 在文件顶部添加导入
from src.services.harness.events.event_serializer import serialize_event, serialize_harness_event
from src.services.harness.events.event_bus import event_bus
from src.services.harness.agents.router_agent import RouterAgent

# 修改 run_agent_stream 函数
@traceable(name="run_agent_stream")
async def run_agent_stream(
    agent_id: str,
    org_id: str,
    client_id: str,
    external_id: str,
    message: str,
    session_service: DatabaseSessionService,
    artifacts_service: InMemoryArtifactService,
    memory_service: InMemoryMemoryService,
    db: AsyncSession,
    session_id: Optional[str] = None,
    files: Optional[list] = None,
) -> AsyncGenerator[str, None]:
    
    # ... 保留原有的初始化代码 ...
    
    try:
        with trace.use_span(span, end_on_exit=True):
            # 1. 路由决策
            target_agent = RouterAgent.route(message)
            logger.info(f"Routing to agent: {target_agent}, org_id: {org_id}, client_id: {client_id}")
            
            # 2. 根据路由选择代理
            # 如果是 lead_agent，使用我们的 harness 代理
            # 否则保持原有逻辑
            
            # ... 保留原有的文件处理、会话管理代码 ...
            
            # 3. 启动任务事件监听
            async def listen_and_yield_task_events():
                """监听任务事件并 yield"""
                queue = await event_bus.subscribe(external_id)
                try:
                    while True:
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=0.1)
                            yield serialize_harness_event(event)
                        except asyncio.TimeoutError:
                            continue
                finally:
                    await event_bus.unsubscribe(external_id, queue)
            
            # 4. 修改事件循环（关键修改）
            try:
                events_async = agent_runner.run_async(
                    user_id=external_id,
                    session_id=adk_session_id,
                    new_message=content,
                )

                # 创建任务监听任务
                event_listener_task = asyncio.create_task(listen_and_yield_task_events())
                
                try:
                    # 处理 ADK 事件
                    async for event in events_async:
                        try:
                            # 关键：替换原有的 yield json.dumps(event_dict)
                            yield serialize_event(event)
                        except Exception as e:
                            logger.error(f"Error processing event: {e}")
                            continue
                            
                    # 等待一小段时间以获取剩余的任务事件
                    await asyncio.sleep(0.5)
                    
                finally:
                    # 取消事件监听
                    event_listener_task.cancel()
                    try:
                        await event_listener_task
                    except asyncio.CancelledError:
                        pass

                # ... 保留原有的会话完成处理 ...
                
            except Exception as e:
                logger.error(f"Error processing request: {str(e)}")
                raise InternalServerError(str(e)) from e
            finally:
                # ... 保留原有的清理代码 ...
                
    finally:
        span.end()
```

### 4.2 WebSocket 端点增强

创建或修改 `src/api/websocket.py`（补充多租户参数校验）：

```Python

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import logging

from src.services.harness.events.event_bus import event_bus
from src.services.harness.events.event_serializer import serialize_harness_event
from src.services.harness.runtime.task_runtime import get_task_runtime
# 复用项目原生DB会话依赖
from src.database.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

@router.websocket("/ws/chat")
async def websocket_chat_endpoint(
    websocket: WebSocket,
    org_id: str,
    client_id: str,
    user_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db)
):
    await websocket.accept()
    logger.info(f"WebSocket connected: org={org_id}, client={client_id}, user={user_id}, session={session_id}")
    
    # 订阅事件
    event_queue = await event_bus.subscribe(user_id)
    
    try:
        # 启动事件监听任务
        async def listen_for_events():
            while True:
                try:
                    event = await event_queue.get()
                    await websocket.send_text(serialize_harness_event(event))
                except Exception as e:
                    logger.error(f"Error sending event: {e}")
        
        event_listener = asyncio.create_task(listen_for_events())
        
        try:
            # 主消息循环
            while True:
                data = await websocket.receive_json()
                
                # 处理不同类型的消息
                msg_type = data.get("type")
                
                if msg_type == "user_message":
                    # 调用 run_agent_stream 并流式返回
                    message = data.get("message", "")
                    files = data.get("files", [])
                    
                    # 这里调用你的 run_agent_stream 函数，透传多租户参数
                    # async for chunk in run_agent_stream(org_id=org_id, client_id=client_id, ...):
                    #     await websocket.send_text(chunk)
                    pass
                    
                elif msg_type == "cancel_task":
                    # 处理任务取消（带多租户校验）
                    task_id = data.get("task_id")
                    runtime = get_task_runtime()
                    success = await runtime.cancel_task(task_id, org_id, client_id, user_id)
                    
                    await websocket.send_text(serialize_harness_event({
                        "type": "system",
                        "content": f"Task {task_id} cancelled: {success}"
                    }))
                    
                elif msg_type == "get_task_status":
                    # 查询任务状态（带多租户校验）
                    task_id = data.get("task_id")
                    runtime = get_task_runtime()
                    task = await runtime.get_task_status(task_id, org_id, client_id, user_id)
                    
                    if task:
                        await websocket.send_text(serialize_harness_event({
                            "type": "task_update",
                            "task_id": task_id,
                            "status": task.status,
                            "progress": task.progress
                        }))
                
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: user={user_id}")
        finally:
            event_listener.cancel()
            try:
                await event_listener
            except asyncio.CancelledError:
                pass
                
    finally:
        await event_bus.unsubscribe(user_id, event_queue)
```

### 4.3 FastAPI 启动/关闭事件集成

在项目原生的 `src/main.py` 中添加事件总线和任务运行时的初始化：

```Python

from fastapi import FastAPI
from src.services.harness.events.event_bus import event_bus
from src.services.harness.runtime.task_runtime import init_task_runtime
from src.database.session import async_session

app = FastAPI()

# ... 保留原有的项目代码 ...

@app.on_event("startup")
async def startup_event():
    # 初始化事件总线
    await event_bus.initialize()
    # 初始化任务运行时
    db = async_session()
    init_task_runtime(db)
    # 恢复未完成的任务
    runtime = get_task_runtime()
    await runtime.resume_pending_tasks()
    # ... 保留原有的其他启动逻辑 ...

@app.on_event("shutdown")
async def shutdown_event():
    # 关闭事件总线
    await event_bus.close()
    # ... 保留原有的其他关闭逻辑 ...
```

---

## 5. 生产级加固（第 10-14 天）

### 5.1 任务队列化（Celery + Redis，已优化：新增死信队列DLQ）

#### 5.1.1 Celery 配置 (`src/services/harness/runtime/celery_app.py`)

优化点：新增死信队列(DLQ)配置，处理多次重试失败的任务，避免任务丢失

```Python

import os
from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue
from src.core.config import settings

# 交换机定义
TASK_EXCHANGE = Exchange('harness.tasks', type='direct')
# 死信交换机定义
DLQ_EXCHANGE = Exchange('harness.dlx', type='direct')

# 队列定义
task_queues = [
    # 主任务队列，配置死信交换机
    Queue(
        'harness_default',
        TASK_EXCHANGE,
        routing_key='harness.default',
        queue_arguments={
            'x-dead-letter-exchange': 'harness.dlx',
            'x-dead-letter-routing-key': 'harness.dead'
        }
    ),
    # 死信队列
    Queue(
        'harness_dlq',
        DLQ_EXCHANGE,
        routing_key='harness.dead'
    )
]

celery_app = Celery(
    'harness_tasks',
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=['src.services.harness.runtime.celery_tasks']
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    # 任务超时配置
    task_time_limit=30 * 60,  # 硬超时30分钟
    task_soft_time_limit=25 * 60,  # 软超时25分钟
    # 队列配置
    task_queues=task_queues,
    task_default_queue='harness_default',
    task_default_exchange='harness.tasks',
    task_default_routing_key='harness.default',
    # 重试配置：最多重试3次，指数退避
    task_max_retries=3,
    task_default_retry_delay=10,
    task_retry_backoff=True,
    task_retry_backoff_max=60,
    # Worker配置
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    worker_enable_remote_control=True,
)

# 定时任务
celery_app.conf.beat_schedule = {
    'cleanup-completed-tasks': {
        'task': 'src.services.harness.runtime.celery_tasks.cleanup_completed_tasks',
        'schedule': crontab(hour=2, minute=0),  # 每天凌晨2点
    },
    'dlq-task-monitor': {
        'task': 'src.services.harness.runtime.celery_tasks.monitor_dlq_tasks',
        'schedule': crontab(minute='*/5'),  # 每5分钟检查一次死信队列
    }
}
```

#### 5.1.2 Celery 任务定义 (`src/services/harness/runtime/celery_tasks.py`)

优化点：完善重试逻辑、死信队列监控、任务状态同步

```Python

import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from celery import current_task

from src.services.harness.runtime.celery_app import celery_app
from src.services.harness.models.task_model import Task
from src.database.session import sync_session
from src.services.harness.events.event_bus import event_bus

logger = logging.getLogger(__name__)

@celery_app.task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    queue='harness_default'
)
def execute_long_running_task(self, task_id: str, input: dict):
    """
    Celery 任务：执行长时工作
    支持重试和状态追踪，重试失败后自动进入死信队列
    """
    logger.info(f"Celery task starting: {task_id}, retry count: {self.request.retries}")
    
    # 获取同步DB session
    db = sync_session()
    
    try:
        # 1. 更新任务状态
        task = db.get(Task, task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return {"status": "error", "message": "Task not found"}
        
        task.status = "running"
        task.resumable_state = {"celery_task_id": self.request.id}
        db.commit()
        db.refresh(task)
        
        # 推送任务开始事件
        asyncio.run(event_bus.publish(task.user_id, {
            "type": "task_started",
            "task_id": task_id,
            "celery_task_id": self.request.id
        }))

        # 2. 执行实际工作
        # 这里调用你的同步工作流代码
        result = _do_actual_work(input, task_id)
        
        # 3. 任务完成
        task.status = "completed"
        task.output = result
        db.commit()
        
        # 推送完成事件
        asyncio.run(event_bus.publish(task.user_id, {
            "type": "task_completed",
            "task_id": task_id,
            "output": result
        }))
        
        logger.info(f"Celery task completed: {task_id}")
        return result
        
    except Exception as e:
        logger.error(f"Celery task failed: {task_id}, error: {e}, retry count: {self.request.retries}", exc_info=True)
        
        # 更新任务状态
        task = db.get(Task, task_id)
        if task:
            # 如果达到最大重试次数，标记为最终失败
            if self.request.retries >= self.max_retries:
                task.status = "failed"
                task.output = {"error": str(e), "celery_task_id": self.request.id, "retries": self.request.retries}
                db.commit()
                
                # 推送失败事件
                asyncio.run(event_bus.publish(task.user_id, {
                    "type": "task_failed",
                    "task_id": task_id,
                    "error": str(e),
                    "retries_exhausted": True
                }))
            else:
                # 重试中，更新状态
                task.status = "retrying"
                task.output = {"error": str(e), "retry_count": self.request.retries}
                db.commit()
        
        # 触发重试
        raise self.retry(exc=e)

def _do_actual_work(input: dict, task_id: str) -> dict:
    """实际的工作逻辑"""
    # 替换为你的业务逻辑
    import time
    time.sleep(5)
    return {
        "result": "success",
        "data": "Processed by Celery",
        "task_id": task_id,
        "completed_at": datetime.utcnow().isoformat()
    }

@celery_app.task(queue='harness_default')
def cleanup_completed_tasks():
    """清理已完成的旧任务（保留30天）"""
    logger.info("Running cleanup task...")
    db = sync_session()
    cutoff_date = datetime.utcnow() - timedelta(days=30)
    db.execute(delete(Task).where(
        Task.status.in_(["completed", "failed", "cancelled"]),
        Task.created_at < cutoff_date
    ))
    db.commit()
    logger.info("Cleanup completed")

@celery_app.task(queue='harness_default')
def monitor_dlq_tasks():
    """监控死信队列，记录告警日志"""
    from src.core.config import settings
    from redis import Redis

    redis_client = Redis.from_url(settings.REDIS_URL)
    # 获取死信队列长度
    dlq_length = redis_client.llen('harness_dlq')
    
    if dlq_length > 0:
        logger.warning(f"Dead letter queue has {dlq_length} failed tasks, please check!")
        # 这里可以接入告警系统，发送邮件/钉钉/企业微信告警
    else:
        logger.info("Dead letter queue is empty")
    
    return {"dlq_length": dlq_length}

@celery_app.task(queue='harness_default')
def reprocess_dlq_task(task_id: str):
    """重新处理死信队列中的任务"""
    logger.info(f"Reprocessing DLQ task: {task_id}")
    db = sync_session()
    task = db.get(Task, task_id)
    
    if not task:
        logger.error(f"Task {task_id} not found in DLQ")
        return {"status": "error", "message": "Task not found"}
    
    # 重置任务状态
    task.status = "pending"
    task.output = None
    task.progress = 0.0
    db.commit()
    
    # 重新提交任务
    execute_long_running_task.delay(task_id, task.input)
    logger.info(f"Task {task_id} reprocessed successfully")
    return {"status": "success", "message": f"Task {task_id} reprocessed"}
```

### 5.2 可观测性

#### 5.2.1 结构化日志 (`src/services/harness/config/logging.py`)

```Python

import structlog
import logging
import sys
from datetime import datetime

def configure_logging():
    """配置结构化日志，兼容项目原生日志体系"""
    
    # 配置标准库 logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    
    # 配置 structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

# 获取 logger
def get_logger(name: str):
    return structlog.get_logger(name)
```

#### 5.2.2 Prometheus Metrics (`src/services/harness/config/metrics.py`)

```Python

from prometheus_client import Counter, Histogram, Gauge, start_http_server
import time

# 指标定义
TASK_STARTED = Counter('harness_tasks_started_total', 'Total tasks started', ['org_id', 'agent_name'])
TASK_COMPLETED = Counter('harness_tasks_completed_total', 'Total tasks completed', ['org_id', 'agent_name', 'status'])
TASK_DURATION = Histogram('harness_task_duration_seconds', 'Task duration', ['org_id', 'agent_name'])
ACTIVE_TASKS = Gauge('harness_tasks_active', 'Number of active tasks', ['org_id', 'agent_name'])
WS_CONNECTIONS = Gauge('harness_websocket_connections', 'Number of active WebSocket connections')
DLQ_TASKS = Gauge('harness_dlq_tasks_total', 'Number of tasks in dead letter queue')

def start_metrics_server(port: int = 9090):
    """启动 Prometheus metrics 服务器"""
    start_http_server(port)
    print(f"Metrics server started on port {port}")

# 装饰器：记录任务指标
def track_task(agent_name: str, org_id: str = "default"):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            TASK_STARTED.labels(org_id=org_id, agent_name=agent_name).inc()
            ACTIVE_TASKS.labels(org_id=org_id, agent_name=agent_name).inc()
            
            start_time = time.time()
            status = "success"
            
            try:
                return await func(*args, **kwargs)
            except Exception:
                status = "failed"
                raise
            finally:
                duration = time.time() - start_time
                TASK_DURATION.labels(org_id=org_id, agent_name=agent_name).observe(duration)
                TASK_COMPLETED.labels(org_id=org_id, agent_name=agent_name, status=status).inc()
                ACTIVE_TASKS.labels(org_id=org_id, agent_name=agent_name).dec()
                
        return wrapper
    return decorator
```

### 5.3 安全性加固

#### 5.3.1 API Key 管理（复用项目原生加密服务）

```Python

# 直接复用项目原生的加密服务，无需重复实现
from src.core.security import encrypt_api_key, decrypt_api_key

# 若项目无原生加密服务，使用以下实现
from cryptography.fernet import Fernet
import os
from src.core.config import settings

class EncryptionService:
    def __init__(self):
        key = settings.ENCRYPTION_KEY
        if not key:
            raise ValueError("ENCRYPTION_KEY not set in environment")
        self.cipher = Fernet(key)
    
    def encrypt(self, plaintext: str) -> str:
        return self.cipher.encrypt(plaintext.encode()).decode()
    
    def decrypt(self, ciphertext: str) -> str:
        return self.cipher.decrypt(ciphertext.encode()).decode()

encryption_service = EncryptionService()
```

#### 5.3.2 文件上传安全

```Python

import magic
import os
from typing import Optional

# 允许的 MIME 类型
ALLOWED_MIME_TYPES = {
    'application/pdf',
    'text/plain',
    'text/csv',
    'application/json',
    'image/png',
    'image/jpeg'
}

# 最大文件大小 (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024

def validate_file(file_data: bytes, filename: str, content_type: str) -> Optional[str]:
    """
    验证上传文件
    返回: 错误信息，如果验证通过返回 None
    """
    # 1. 检查大小
    if len(file_data) > MAX_FILE_SIZE:
        return f"File too large. Max size is {MAX_FILE_SIZE / 1024 / 1024}MB"
    
    # 2. 检查 MIME 类型
    try:
        detected_type = magic.from_buffer(file_data, mime=True)
        if detected_type not in ALLOWED_MIME_TYPES:
            return f"File type {detected_type} not allowed"
    except Exception as e:
        return f"Failed to validate file type: {e}"
    
    # 3. 检查文件扩展名
    ext = os.path.splitext(filename)[1].lower()
    allowed_exts = {'.pdf', '.txt', '.csv', '.json', '.png', '.jpg', '.jpeg'}
    if ext not in allowed_exts:
        return f"File extension {ext} not allowed"
    
    return None
```

---

## 6. 部署与运维

### 6.1 Docker 容器化

#### 6.1.1 Dockerfile（对齐项目原生构建规范）

```Dockerfile

# 多阶段构建
FROM python:3.11-slim as builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# 最终镜像
FROM python:3.11-slim

WORKDIR /app

# 安装运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制构建的依赖
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# 复制应用代码
COPY . .

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# 暴露端口
EXPOSE 8000

# 启动命令（对齐项目原生启动命令）
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

#### 6.1.2 docker-compose.yml（对齐项目原生服务）

```YAML

version: '3.8'

services:
  api:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      - POSTGRES_CONNECTION_STRING=postgresql://postgres:postgres@db:5432/evo_ai
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
    volumes:
      - ./logs:/app/logs
    deploy:
      replicas: 2
      restart_policy:
        condition: on-failure

  celery_worker:
    build: .
    command: celery -A src.services.harness.runtime.celery_app worker --loglevel=info --concurrency=4
    env_file:
      - .env
    environment:
      - POSTGRES_CONNECTION_STRING=postgresql://postgres:postgres@db:5432/evo_ai
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis
    deploy:
      replicas: 2

  celery_beat:
    build: .
    command: celery -A src.services.harness.runtime.celery_app beat --loglevel=info
    env_file:
      - .env
    environment:
      - POSTGRES_CONNECTION_STRING=postgresql://postgres:postgres@db:5432/evo_ai
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - db
      - redis

  db:
    image: postgres:15-alpine
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB=evo_ai
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  prometheus:
    image: prom/prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana

volumes:
  postgres_data:
  redis_data:
  prometheus_data:
  grafana_data:
```

### 6.2 生产环境检查清单

<checkbox id="1" done="false">数据库连接池配置（asyncpg）</checkbox>

<checkbox id="2" done="false">Redis 高可用（Sentinel 或 Cluster）</checkbox>

<checkbox id="3" done="false">HTTPS 证书配置</checkbox>

<checkbox id="4" done="false">JWT Secret Key 轮换机制</checkbox>

<checkbox id="5" done="false">ENCRYPTION_KEY 安全存储</checkbox>

<checkbox id="6" done="false">日志聚合（ELK / Loki）</checkbox>

<checkbox id="7" done="false">告警规则配置（Prometheus Alertmanager）</checkbox>

<checkbox id="8" done="false">数据库定时备份策略</checkbox>

<checkbox id="9" done="false">灾难恢复预案</checkbox>

<checkbox id="10" done="false">死信队列监控与告警</checkbox>

<checkbox id="11" done="false">性能压测报告</checkbox>

<checkbox id="12" done="false">多租户权限测试</checkbox>

---

## 7. 附录

### 7.1 完整代码生成脚本

保存为 `generate_harness_repo.sh`：

```Bash

#!/bin/bash
set -e

echo "🚀 Generating Harness Agent Runtime structure..."

# 创建目录
mkdir -p src/services/harness/{agents,runtime,tools,events,models,skills/example_skill,config}
mkdir -p migrations/versions
mkdir -p src/api

# 创建 README
cat << 'EOF' > src/services/harness/README.md
# Harness Agent Runtime
生产级多代理工作流运行时系统
## 模块说明
- agents: 代理实现（路由、主协调、任务、简单代理）
- runtime: 任务运行时、Celery队列、事件流
- tools: 长时任务工具、Skills加载器
- events: 事件类型、序列化、分布式事件总线
- models: 数据模型
- config: 日志、指标、分布式锁配置
EOF

echo "✅ Harness structure generated successfully!"
echo "📁 Location: $(pwd)/src/services/harness"
```

### 7.2 参考资料

- Google ADK Documentation: [https://google.github.io/adk-docs/](https://google.github.io/adk-docs/)

- Google ADK Samples: [https://github.com/google/adk-python](https://github.com/google/adk-python)

- Eigent Workforce: [https://github.com/eigent-ai/eigent](https://github.com/eigent-ai/eigent)

- Celery Documentation: [https://docs.celeryq.dev/](https://docs.celeryq.dev/)

- Prometheus Python Client: [https://github.com/prometheus/client_python](https://github.com/prometheus/client_python)

- my_evo_ai 原生仓库: [https://github.com/RoyLLLL/my_evo_ai](https://github.com/RoyLLLL/my_evo_ai)

---

## 总结

本指南已完成所有优化点的落地，将 `my_evo_ai` 改造为生产级 Harness Agent Runtime 的完整路径如下：

1. **第 1-2 天**：准备工作与环境搭建，梳理原生项目架构

2. **第 3-7 天**：核心架构植入（多租户数据模型、分布式事件总线、全异步任务运行时、分布式锁）

3. **第 8-9 天**：与现有系统集成，对齐原生多租户、异步DB、Redis体系

4. **第 10-14 天**：生产级加固（死信队列、可观测性、安全、容器化、高可用部署）

改造后的系统完全兼容项目原生架构，支持分布式集群部署，具备企业级应用的高可用、多租户安全、任务可靠性、可观测特性。

