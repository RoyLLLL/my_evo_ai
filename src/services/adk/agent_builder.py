"""
┌──────────────────────────────────────────────────────────────────────────────┐
│ @author: Davidson Gomes                                                      │
│ @file: agent_builder.py                                                      │
│ Developed by: Davidson Gomes                                                 │
│ Creation date: May 13, 2025                                                  │
│ Contact: contato@evolution-api.com                                           │
├──────────────────────────────────────────────────────────────────────────────┤
│ @copyright © Evolution API 2025. All rights reserved.                        │
│ Licensed under the Apache License, Version 2.0                               │
│                                                                              │
│ You may not use this file except in compliance with the License.             │
│ You may obtain a copy of the License at                                      │
│                                                                              │
│    http://www.apache.org/licenses/LICENSE-2.0                                │
│                                                                              │
│ Unless required by applicable law or agreed to in writing, software          │
│ distributed under the License is distributed on an "AS IS" BASIS,            │
│ WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.     │
│ See the License for the specific language governing permissions and          │
│ limitations under the License.                                               │
├──────────────────────────────────────────────────────────────────────────────┤
│ @important                                                                   │
│ For any future changes to the code in this file, it is recommended to        │
│ include, together with the modification, the information of the developer    │
│ who changed it and the date of modification.                                 │
└──────────────────────────────────────────────────────────────────────────────┘
"""

from typing import List, Optional, Tuple
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents import SequentialAgent, ParallelAgent, LoopAgent, BaseAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool
from src.schemas.schemas import Agent
from src.utils.logger import setup_logger
from src.core.exceptions import AgentNotFoundError
from src.services.agent_service import get_agent
from src.services.adk.custom_tools import CustomToolBuilder
from src.services.adk.mcp_service import MCPService
from src.services.adk.custom_agents.a2a_agent import A2ACustomAgent
from src.services.adk.custom_agents.workflow_agent import WorkflowAgent
from src.services.adk.custom_agents.task_agent import TaskAgent
from src.services.adk.super_agent.super_agent import build_super_agent
from src.services.adk.super_agent.event_bus import EventBus
from src.services.apikey_service import get_decrypted_api_key
from src.config.settings import settings
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import AsyncExitStack
from google.adk.tools import load_memory

from datetime import datetime
import uuid

from src.schemas.agent_config import AgentTask

logger = setup_logger(__name__)


class AgentBuilder:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.custom_tool_builder = CustomToolBuilder()
        self.mcp_service = MCPService()

    async def _agent_tools_builder(self, agent) -> List[AgentTool]:
        """Build the tools for an agent."""
        agent_tools_ids = agent.config.get("agent_tools")
        agent_tools = []
        if agent_tools_ids and isinstance(agent_tools_ids, list):
            for agent_tool_id in agent_tools_ids:
                sub_agent = get_agent(self.db, agent_tool_id)
                llm_agent, _ = await self.build_llm_agent(sub_agent)
                if llm_agent:
                    agent_tools.append(AgentTool(agent=llm_agent))
        return agent_tools

    async def _create_llm_agent(
        self, agent, enabled_tools: List[str] = []
    ) -> Tuple[LlmAgent, Optional[AsyncExitStack]]:
        """Create an LLM agent from the agent data."""
        # Get custom tools from the configuration
        custom_tools = []
        custom_tools = self.custom_tool_builder.build_tools(agent.config)

        # Get MCP tools from the configuration
        mcp_tools = []
        mcp_exit_stack = None
        if agent.config.get("mcp_servers") or agent.config.get("custom_mcp_servers"):
            mcp_tools, mcp_exit_stack = await self.mcp_service.build_tools(
                agent.config, self.db
            )

        # Get agent tools
        agent_tools = await self._agent_tools_builder(agent)

        # Combine all tools
        all_tools = custom_tools + mcp_tools + agent_tools

        if enabled_tools:
            all_tools = [tool for tool in all_tools if tool.name in enabled_tools]
            logger.info(f"Enabled tools enabled. Total tools: {len(all_tools)}")

        now = datetime.now()
        current_datetime = now.strftime("%d/%m/%Y %H:%M")
        current_day_of_week = now.strftime("%A")
        current_date_iso = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")

        # Substitute variables in the prompt
        formatted_prompt = agent.instruction.format(
            current_datetime=current_datetime,
            current_day_of_week=current_day_of_week,
            current_date_iso=current_date_iso,
            current_time=current_time,
        )

        # add role on beginning of the prompt
        if agent.role:
            formatted_prompt = (
                f"<agent_role>{agent.role}</agent_role>\n\n{formatted_prompt}"
            )

        # add goal on beginning of the prompt
        if agent.goal:
            formatted_prompt = (
                f"<agent_goal>{agent.goal}</agent_goal>\n\n{formatted_prompt}"
            )

        # Check if load_memory is enabled
        if agent.config.get("load_memory"):
            all_tools.append(load_memory)
            formatted_prompt = (
                formatted_prompt
                + "\n\n<memory_instructions>ALWAYS use the load_memory tool to retrieve knowledge for your context</memory_instructions>\n\n"
            )

        api_key = settings.DEEPSEEK_API_KEY
        # Resolve model: fall back to DeepSeek default if not set
        model_name = settings.DEEPSEEK_DEFAULT_MODEL

        # Build LiteLlm kwargs; pass api_base for DeepSeek default
        litellm_kwargs = {"model": model_name, "api_key": api_key}
        if not (agent.model or "").strip() or (
            api_key == settings.DEEPSEEK_API_KEY and settings.DEEPSEEK_BASE_URL
        ):
            litellm_kwargs["api_base"] = settings.DEEPSEEK_BASE_URL

        return (
            LlmAgent(
                name=agent.name,
                model=LiteLlm(**litellm_kwargs),
                instruction=formatted_prompt,
                description=agent.description,
                tools=all_tools,
            ),
            mcp_exit_stack,
        )

    async def _get_sub_agents(
        self, sub_agent_ids: List[str]
    ) -> List[Tuple[LlmAgent, Optional[AsyncExitStack]]]:
        """Get and create LLM sub-agents."""
        sub_agents = []
        for sub_agent_id in sub_agent_ids:
            sub_agent_id_str = str(sub_agent_id)

            agent = get_agent(self.db, sub_agent_id_str)

            if agent is None:
                logger.error(f"Sub-agent not found: {sub_agent_id_str}")
                raise AgentNotFoundError(f"Agent with ID {sub_agent_id_str} not found")

            logger.info(f"Sub-agent found: {agent.name} (type: {agent.type})")

            if agent.type == "llm":
                sub_agent, exit_stack = await self._create_llm_agent(agent)
            elif agent.type == "a2a":
                sub_agent, exit_stack = await self.build_a2a_agent(agent)
            elif agent.type == "workflow":
                sub_agent, exit_stack = await self.build_workflow_agent(agent)
            elif agent.type == "task":
                sub_agent, exit_stack = await self.build_task_agent(agent)
            elif agent.type == "super":
                sub_agent, exit_stack = await self._build_super_agent(agent)
            elif agent.type == "sequential":
                sub_agent, exit_stack = await self.build_composite_agent(agent)
            elif agent.type == "parallel":
                sub_agent, exit_stack = await self.build_composite_agent(agent)
            elif agent.type == "loop":
                sub_agent, exit_stack = await self.build_composite_agent(agent)
            else:
                raise ValueError(f"Invalid agent type: {agent.type}")

            sub_agents.append(sub_agent)
            logger.info(f"Sub-agent added: {agent.name}")

        logger.info(f"Sub-agents created: {len(sub_agents)}")
        logger.info(f"Sub-agents: {str(sub_agents)}")

        return sub_agents

    async def build_llm_agent(
        self, root_agent, enabled_tools: List[str] = []
    ) -> Tuple[LlmAgent, Optional[AsyncExitStack]]:
        """Build an LLM agent with its sub-agents."""
        logger.info("Creating LLM agent")

        sub_agents = []
        if root_agent.config.get("sub_agents"):
            sub_agents_with_stacks = await self._get_sub_agents(
                root_agent.config.get("sub_agents")
            )
            sub_agents = [agent for agent, _ in sub_agents_with_stacks]

        root_llm_agent, exit_stack = await self._create_llm_agent(
            root_agent, enabled_tools
        )
        if sub_agents:
            root_llm_agent.sub_agents = sub_agents

        return root_llm_agent, exit_stack

    async def build_a2a_agent(
        self, root_agent
    ) -> Tuple[BaseAgent, Optional[AsyncExitStack]]:
        """Build an A2A agent with its sub-agents."""
        logger.info(f"Creating A2A agent from {root_agent.agent_card_url}")

        if not root_agent.agent_card_url:
            raise ValueError("agent_card_url is required for a2a agents")

        try:
            sub_agents = []
            if root_agent.config.get("sub_agents"):
                sub_agents_with_stacks = await self._get_sub_agents(
                    root_agent.config.get("sub_agents")
                )
                sub_agents = [agent for agent, _ in sub_agents_with_stacks]

            config = root_agent.config or {}
            timeout = config.get("timeout", 300)

            a2a_agent = A2ACustomAgent(
                name=root_agent.name,
                agent_card_url=root_agent.agent_card_url,
                timeout=timeout,
                description=root_agent.description
                or f"A2A Agent for {root_agent.name}",
                sub_agents=sub_agents,
            )

            logger.info(
                f"A2A agent created successfully: {root_agent.name} ({root_agent.agent_card_url})"
            )

            return a2a_agent, None

        except Exception as e:
            logger.error(f"Error building A2A agent: {str(e)}")
            raise ValueError(f"Error building A2A agent: {str(e)}")

    async def build_workflow_agent(
        self, root_agent
    ) -> Tuple[WorkflowAgent, Optional[AsyncExitStack]]:
        """Build a workflow agent with its sub-agents."""
        logger.info(f"Creating Workflow agent from {root_agent.name}")

        agent_config = root_agent.config or {}

        if not agent_config.get("workflow"):
            raise ValueError("workflow is required for workflow agents")

        try:
            sub_agents = []
            if root_agent.config.get("sub_agents"):
                sub_agents_with_stacks = await self._get_sub_agents(
                    root_agent.config.get("sub_agents")
                )
                sub_agents = [agent for agent, _ in sub_agents_with_stacks]

            config = root_agent.config or {}
            timeout = config.get("timeout", 300)

            workflow_agent = WorkflowAgent(
                name=root_agent.name,
                flow_json=agent_config.get("workflow"),
                timeout=timeout,
                description=root_agent.description
                or f"Workflow Agent for {root_agent.name}",
                sub_agents=sub_agents,
                db=self.db,
            )

            logger.info(f"Workflow agent created successfully: {root_agent.name}")

            return workflow_agent, None

        except Exception as e:
            logger.error(f"Error building Workflow agent: {str(e)}")
            raise ValueError(f"Error building Workflow agent: {str(e)}")

    async def build_task_agent(
        self, root_agent
    ) -> Tuple[TaskAgent, Optional[AsyncExitStack]]:
        """Build a task agent with its sub-agents."""
        logger.info(f"Creating Task agent: {root_agent.name}")

        agent_config = root_agent.config or {}

        if not agent_config.get("tasks"):
            raise ValueError("tasks are required for Task agents")

        try:
            # Get sub-agents if there are any
            sub_agents = []
            if root_agent.config.get("sub_agents"):
                sub_agents_with_stacks = await self._get_sub_agents(
                    root_agent.config.get("sub_agents")
                )
                sub_agents = [agent for agent, _ in sub_agents_with_stacks]

            # Additional configurations
            config = root_agent.config or {}

            # Convert tasks to the expected format by TaskAgent
            tasks = []
            for task_config in config.get("tasks", []):
                task = AgentTask(
                    agent_id=task_config.get("agent_id"),
                    description=task_config.get("description", ""),
                    expected_output=task_config.get("expected_output", ""),
                    enabled_tools=task_config.get("enabled_tools", []),
                )
                tasks.append(task)

            # Create the Task agent
            task_agent = TaskAgent(
                name=root_agent.name,
                tasks=tasks,
                db=self.db,
                sub_agents=sub_agents,
            )

            logger.info(f"Task agent created successfully: {root_agent.name}")

            return task_agent, None

        except Exception as e:
            logger.error(f"Error building Task agent: {str(e)}")
            raise ValueError(f"Error building Task agent: {str(e)}")

    async def build_composite_agent(
        self, root_agent
    ) -> Tuple[SequentialAgent | ParallelAgent | LoopAgent, Optional[AsyncExitStack]]:
        """Build a composite agent (Sequential, Parallel or Loop) with its sub-agents."""
        logger.info(
            f"Processing sub-agents for agent {root_agent.type} (ID: {root_agent.id}, Name: {root_agent.name})"
        )

        if not root_agent.config.get("sub_agents"):
            logger.error(
                f"Sub_agents configuration not found or empty for agent {root_agent.name}"
            )
            raise ValueError(f"Missing sub_agents configuration for {root_agent.name}")

        logger.info(
            f"Sub-agents IDs to be processed: {root_agent.config.get('sub_agents', [])}"
        )

        sub_agents_with_stacks = await self._get_sub_agents(
            root_agent.config.get("sub_agents", [])
        )

        logger.info(
            f"Sub-agents processed: {len(sub_agents_with_stacks)} of {len(root_agent.config.get('sub_agents', []))}"
        )

        sub_agents = [agent for agent, _ in sub_agents_with_stacks]
        logger.info(f"Extracted sub-agents: {[agent.name for agent in sub_agents]}")

        if root_agent.type == "sequential":
            logger.info(f"Creating SequentialAgent with {len(sub_agents)} sub-agents")
            return (
                SequentialAgent(
                    name=root_agent.name,
                    sub_agents=sub_agents,
                    description=root_agent.config.get("description", ""),
                ),
                None,
            )
        elif root_agent.type == "parallel":
            logger.info(f"Creating ParallelAgent with {len(sub_agents)} sub-agents")
            return (
                ParallelAgent(
                    name=root_agent.name,
                    sub_agents=sub_agents,
                    description=root_agent.config.get("description", ""),
                ),
                None,
            )
        elif root_agent.type == "loop":
            logger.info(f"Creating LoopAgent with {len(sub_agents)} sub-agents")
            return (
                LoopAgent(
                    name=root_agent.name,
                    sub_agents=sub_agents,
                    description=root_agent.config.get("description", ""),
                    max_iterations=root_agent.config.get("max_iterations", 5),
                ),
                None,
            )
        else:
            raise ValueError(f"Invalid agent type: {root_agent.type}")

    async def build_agent(self, root_agent, enabled_tools: List[str] = []) -> Tuple[
        LlmAgent
        | SequentialAgent
        | ParallelAgent
        | LoopAgent
        | A2ACustomAgent
        | WorkflowAgent
        | TaskAgent,
        Optional[AsyncExitStack],
    ]:
        """Build the appropriate agent based on the type of the root agent."""
        if root_agent.type == "llm":
            return await self.build_llm_agent(root_agent, enabled_tools)
        elif root_agent.type == "a2a":
            return await self.build_a2a_agent(root_agent)
        elif root_agent.type == "workflow":
            return await self.build_workflow_agent(root_agent)
        elif root_agent.type == "task":
            return await self.build_task_agent(root_agent)
        elif root_agent.type == "super":
            return await self._build_super_agent(root_agent)
        else:
            return await self.build_composite_agent(root_agent)

    async def _build_super_agent(
        self, root_agent, session_id: str = ""
    ) -> Tuple[LlmAgent, Optional[AsyncExitStack]]:
        """Build a SuperAgent with event bus, skills, and sub-agents."""
        logger.info(f"Creating Super agent: {root_agent.name}")

        agent_config = root_agent.config or {}

        # Resolve API key
        api_key = None
        if hasattr(root_agent, "api_key_id") and root_agent.api_key_id:
            api_key = get_decrypted_api_key(self.db, root_agent.api_key_id)
            if not api_key:
                raise ValueError(f"API key with ID {root_agent.api_key_id} not found or inactive")
        else:
            config_api_key = agent_config.get("api_key")
            if config_api_key:
                try:
                    import uuid as _uuid
                    key_id = _uuid.UUID(config_api_key)
                    decrypted = get_decrypted_api_key(self.db, key_id)
                    api_key = decrypted if decrypted else config_api_key
                except (ValueError, TypeError):
                    api_key = config_api_key
            elif settings.DEEPSEEK_API_KEY:
                logger.info(f"Using default DeepSeek API key for super agent {root_agent.name}")
                api_key = settings.DEEPSEEK_API_KEY
            else:
                raise ValueError(f"Agent {root_agent.name} does not have a configured API key")

        # Get sub-agents
        sub_agents = []
        if agent_config.get("sub_agents"):
            sub_agents_with_stacks = await self._get_sub_agents(agent_config["sub_agents"])
            sub_agents = [agent for agent, _ in sub_agents_with_stacks]

        # Get extra tools (custom + MCP + agent_tools)
        extra_tools = []

        custom_tools = self.custom_tool_builder.build_tools(agent_config)
        extra_tools.extend(custom_tools)

        mcp_exit_stack = None
        if agent_config.get("mcp_servers") or agent_config.get("custom_mcp_servers"):
            mcp_tools, mcp_exit_stack = await self.mcp_service.build_tools(agent_config, self.db)
            extra_tools.extend(mcp_tools)

        agent_tools = await self._agent_tools_builder(root_agent)
        extra_tools.extend(agent_tools)

        if agent_config.get("load_memory"):
            extra_tools.append(load_memory)

        # Skills config
        skills = agent_config.get("skills", ["todo", "research"])

        # Format instruction
        now = datetime.now()
        instruction = root_agent.instruction or ""
        if instruction:
            instruction = instruction.format(
                current_datetime=now.strftime("%d/%m/%Y %H:%M"),
                current_day_of_week=now.strftime("%A"),
                current_date_iso=now.strftime("%Y-%m-%d"),
                current_time=now.strftime("%H:%M"),
            )
        if root_agent.role:
            instruction = f"<agent_role>{root_agent.role}</agent_role>\n\n{instruction}"
        if root_agent.goal:
            instruction = f"<agent_goal>{root_agent.goal}</agent_goal>\n\n{instruction}"

        # Resolve model and api_base for super agent
        super_model = (root_agent.model or "").strip() or settings.DEEPSEEK_DEFAULT_MODEL
        super_api_base = None
        if not (root_agent.model or "").strip() or api_key == settings.DEEPSEEK_API_KEY:
            super_api_base = settings.DEEPSEEK_BASE_URL or None

        # Build the super agent
        agent, event_bus, skill_manager = build_super_agent(
            name=root_agent.name,
            model=super_model,
            api_key=api_key,
            api_base=super_api_base,
            instruction=instruction,
            description=root_agent.description or f"Super Agent: {root_agent.name}",
            skills=skills,
            sub_agents=sub_agents,
            extra_tools=extra_tools,
            session_id=session_id,
        )

        logger.info(
            f"Super agent created: {root_agent.name} with skills={skills}, "
            f"sub_agents={len(sub_agents)}, extra_tools={len(extra_tools)}"
        )

        return agent, mcp_exit_stack
