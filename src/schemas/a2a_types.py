"""
┌──────────────────────────────────────────────────────────────────────────────┐
│ @author: Davidson Gomes                                                      │
│ @file: a2a_types.py                                                          │
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

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union, Dict, List, Optional
from uuid import uuid4
from typing_extensions import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_serializer,
    model_validator,
)


class TaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"
    UNKNOWN = "unknown"


class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str
    metadata: dict[str, Any] | None = None


class FileContent(BaseModel):
    name: str | None = None
    mimeType: str | None = None
    bytes: str | None = None
    uri: str | None = None

    @model_validator(mode="after")
    def check_content(self) -> Self:
        if not (self.bytes or self.uri):
            raise ValueError("Either 'bytes' or 'uri' must be present in the file data")
        if self.bytes and self.uri:
            raise ValueError(
                "Only one of 'bytes' or 'uri' can be present in the file data"
            )
        return self


class FilePart(BaseModel):
    kind: Literal["file"] = "file"
    file: FileContent
    metadata: dict[str, Any] | None = None


class DataPart(BaseModel):
    kind: Literal["data"] = "data"
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None


Part = Annotated[TextPart | FilePart | DataPart, Field(discriminator="kind")]


class Message(BaseModel):
    messageId: str = Field(default_factory=lambda: str(uuid4()))
    role: Literal["user", "agent"]
    parts: list[Part]
    contextId: str | None = None
    taskId: str | None = None
    referenceTaskIds: list[str] | None = None
    kind: Literal["message"] = "message"
    metadata: dict[str, Any] | None = None


class TaskStatus(BaseModel):
    state: TaskState
    message: Message | None = None
    timestamp: datetime = Field(default_factory=datetime.now)

    @field_serializer("timestamp")
    def serialize_dt(self, dt: datetime, _info):
        return dt.isoformat()


class Artifact(BaseModel):
    artifactId: str = Field(default_factory=lambda: str(uuid4()))
    parts: list[Part]
    name: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None


class Task(BaseModel):
    id: str
    contextId: str
    status: TaskStatus
    history: list[Message] | None = None
    artifacts: list[Artifact] | None = None
    kind: Literal["task"] = "task"
    metadata: dict[str, Any] | None = None


class TaskStatusUpdateEvent(BaseModel):
    taskId: str
    contextId: str
    status: TaskStatus
    final: bool = False
    kind: Literal["status-update"] = "status-update"
    metadata: dict[str, Any] | None = None


class TaskArtifactUpdateEvent(BaseModel):
    taskId: str
    contextId: str
    artifact: Artifact
    final: bool = False
    kind: Literal["artifact-update"] = "artifact-update"
    metadata: dict[str, Any] | None = None


class AuthenticationInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    schemes: list[str]
    credentials: str | None = None


class PushNotificationConfig(BaseModel):
    url: str
    token: str | None = None
    authentication: AuthenticationInfo | None = None


class TaskIdParams(BaseModel):
    id: str
    metadata: dict[str, Any] | None = None


class TaskQueryParams(TaskIdParams):
    historyLength: int | None = None


class TaskSendParams(BaseModel):
    id: str
    sessionId: str = Field(default_factory=lambda: uuid4().hex)
    message: Message
    acceptedOutputModes: list[str] | None = None
    pushNotification: PushNotificationConfig | None = None
    historyLength: int | None = None
    metadata: dict[str, Any] | None = None


class TaskPushNotificationConfig(BaseModel):
    id: str
    pushNotificationConfig: PushNotificationConfig


# RPC Messages


class JSONRPCMessage(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = Field(default_factory=lambda: uuid4().hex)


class JSONRPCRequest(JSONRPCMessage):
    method: str
    params: dict[str, Any] | None = None


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Any | None = None


class JSONRPCResponse(JSONRPCMessage):
    result: Any | None = None
    error: JSONRPCError | None = None


class SendTaskRequest(JSONRPCRequest):
    method: Literal["tasks/send"] = "tasks/send"
    params: TaskSendParams


class SendTaskResponse(JSONRPCResponse):
    result: Task | None = None


class SendTaskStreamingRequest(JSONRPCRequest):
    method: Literal["tasks/sendSubscribe"] = "tasks/sendSubscribe"
    params: TaskSendParams


class SendTaskStreamingResponse(JSONRPCResponse):
    result: TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None = None


class GetTaskRequest(JSONRPCRequest):
    method: Literal["tasks/get"] = "tasks/get"
    params: TaskQueryParams


class GetTaskResponse(JSONRPCResponse):
    result: Task | None = None


class CancelTaskRequest(JSONRPCRequest):
    method: Literal["tasks/cancel",] = "tasks/cancel"
    params: TaskIdParams


class CancelTaskResponse(JSONRPCResponse):
    result: Task | None = None


class SetTaskPushNotificationRequest(JSONRPCRequest):
    method: Literal["tasks/pushNotification/set",] = "tasks/pushNotification/set"
    params: TaskPushNotificationConfig


class SetTaskPushNotificationResponse(JSONRPCResponse):
    result: TaskPushNotificationConfig | None = None


class GetTaskPushNotificationRequest(JSONRPCRequest):
    method: Literal["tasks/pushNotification/get",] = "tasks/pushNotification/get"
    params: TaskIdParams


class GetTaskPushNotificationResponse(JSONRPCResponse):
    result: TaskPushNotificationConfig | None = None


class TaskResubscriptionRequest(JSONRPCRequest):
    method: Literal["tasks/resubscribe",] = "tasks/resubscribe"
    params: TaskIdParams


A2ARequest = TypeAdapter(
    Annotated[
        SendTaskRequest
        | GetTaskRequest
        | CancelTaskRequest
        | SetTaskPushNotificationRequest
        | GetTaskPushNotificationRequest
        | TaskResubscriptionRequest
        | SendTaskStreamingRequest,
        Field(discriminator="method"),
    ]
)

# Error types


class JSONParseError(JSONRPCError):
    code: int = -32700
    message: str = "Invalid JSON payload"
    data: Any | None = None


class InvalidRequestError(JSONRPCError):
    code: int = -32600
    message: str = "Request payload validation error"
    data: Any | None = None


class MethodNotFoundError(JSONRPCError):
    code: int = -32601
    message: str = "Method not found"
    data: None = None


class InvalidParamsError(JSONRPCError):
    code: int = -32602
    message: str = "Invalid parameters"
    data: Any | None = None


class InternalError(JSONRPCError):
    code: int = -32603
    message: str = "Internal error"
    data: Any | None = None


class TaskNotFoundError(JSONRPCError):
    code: int = -32001
    message: str = "Task not found"
    data: None = None


class TaskNotCancelableError(JSONRPCError):
    code: int = -32002
    message: str = "Task cannot be canceled"
    data: None = None


class PushNotificationNotSupportedError(JSONRPCError):
    code: int = -32003
    message: str = "Push Notification is not supported"
    data: None = None


class UnsupportedOperationError(JSONRPCError):
    code: int = -32004
    message: str = "This operation is not supported"
    data: None = None


class ContentTypeNotSupportedError(JSONRPCError):
    code: int = -32005
    message: str = "Incompatible content types"
    data: None = None


class AgentProvider(BaseModel):
    organization: str
    url: str | None = None


class AgentCapabilities(BaseModel):
    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False


class AgentAuthentication(BaseModel):
    schemes: list[str]
    credentials: str | None = None


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str | None = None
    tags: list[str] | None = None
    examples: list[str] | None = None
    inputModes: list[str] | None = None
    outputModes: list[str] | None = None


class AgentCard(BaseModel):
    name: str
    description: str | None = None
    url: str
    provider: AgentProvider | None = None
    version: str
    documentationUrl: str | None = None
    capabilities: AgentCapabilities
    authentication: AgentAuthentication | None = None
    defaultInputModes: list[str] = ["text"]
    defaultOutputModes: list[str] = ["text"]
    skills: list[AgentSkill]


class A2AClientError(Exception):
    pass


class A2AClientHTTPError(A2AClientError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP Error {status_code}: {message}")


class A2AClientJSONError(A2AClientError):
    def __init__(self, message: str):
        self.message = message
        super().__init__(f"JSON Error: {message}")


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""
