# Agentic Rollout Library

A powerful and modular Python library for building agentic systems with advanced tool execution, LLM integration, and Kubernetes support.

## Features

### Core Architecture

- **Modular Node System**: Built on an extensible BaseNode architecture with specialized nodes for different tasks
- **Timeline Tracking**: Built-in performance profiling and execution tracking for debugging and optimization
- **Async Support**: Full asynchronous support with retry mechanisms and timeout controls
- **Flexible Tool Framework**: Extensible tool system supporting multiple execution modes (local, K8S)

### Key Components

#### 1. Node System (`src/core/`)

- **BaseNode**: Abstract base class providing common functionality for all nodes
  - Timeline tracking integration
  - Timeout support
  - Metadata management
  - Async execution with `process_async()`

- **LLMNode**: Language model integration node
  - Support for multiple LLM providers (OpenAI, Anthropic/Bedrock, DeepSeek, etc.)
  - Configurable retry logic with exponential backoff
  - Connection pooling for better performance
  - Token usage tracking

- **ToolExecutionNode**: Execute tools in local or containerized environments
  - Dynamic tool registration
  - Subprocess-based tool execution
  - Custom result parsers
  - Built-in stop tool support

- **K8SToolExecutionNode**: Kubernetes-based tool execution (extends ToolExecutionNode)
  - Execute tools in Kubernetes pods using [Kodo](https://github.com/your-kodo-repo)
  - Lazy pod initialization for better resource utilization
  - Support for local and K8S execution modes
  - Automatic file synchronization to pods
  - Configurable resource requests (CPU, memory)
  - Custom DNS configuration support

- **ContextEngineeringNode**: Conversation context management
  - Message history tracking
  - Context compression strategies
  - LLM-ready context formatting
  - Message filtering and manipulation

- **ToolParsingNode**: Parse tool calls from LLM responses
  - Support for multiple formats (JSON, XML, OpenAI, Anthropic, LangChain)
  - Custom parser support
  - Automatic validation of tool calls
  - Extensible parsing strategies

#### 2. Tool Collections (`src/tools/`)

**Base Tool Framework** (`src/tools/base_tool.py`)
- Tool wrapper for script-based tools
- Configurable execution modes
- Custom result parsers

**R2E Tools** (`src/tools/r2e/`)
- File editor tool
- Search functionality
- Bash execution
- Finish/completion tool

**Miaoda Tools** (`src/tools/miaoda/`)
- File editor
- Bash executor
- Think tool (reasoning)
- Image search via MCP server
- API RAG (Retrieval-Augmented Generation)
- API description query
- Supabase integration (init, migration, SQL execution)

**DeepSeek Tools** (`src/tools/deepseek/`)
- File editor
- Bash executor

#### 3. Utilities (`src/utils/`)

- **LLM API Utils** (`llm_api_utils.py`)
  - Generic LLM API calling functions
  - Support for OpenAI-compatible APIs
  - DeepSeek API integration
  - Proxy management
  - Streaming support

- **Bedrock Claude Handler** (`bedrock_claude_handle.py`)
  - AWS Bedrock integration for Claude models
  - Async and sync interfaces
  - Token usage tracking
  - Custom endpoint support

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd agentic-rollout-library

# Install dependencies
pip install -r requirements.txt

# For Kubernetes support, install Kodo
pip install kodo
```

## Quick Start

### Example 1: Basic LLM Node Usage

```python
from src.core.llm_node import LLMNode
from src.utils.llm_api_utils import call_llm_api

# Create LLM node with custom function
llm_node = LLMNode(
    name="MyLLMNode",
    model_config={
        "model": "gpt-4",
        "api_key": "your-api-key",
        "base_url": "https://api.openai.com/v1"
    },
    timeline_enabled=True
)

# Set up the LLM function handle
def my_llm_function(messages):
    return call_llm_api(
        messages=messages,
        model=llm_node.model_config["model"],
        api_key=llm_node.model_config["api_key"],
        base_url=llm_node.model_config["base_url"]
    )

llm_node.set_function_handle(my_llm_function)

# Process messages
messages = [{"role": "user", "content": "Hello!"}]
response = llm_node.process(messages)
print(response)
```

### Example 2: Tool Execution with K8S

```python
import asyncio
from src.core import K8SToolExecutionNode

async def main():
    # Create K8S executor
    executor = K8SToolExecutionNode(
        name="K8SExecutor",
        namespace="default",
        kubeconfig_path="~/.kube/config",
        image="python:3.9",
        pod_name="my-tool-pod",
        timeline_enabled=True
    )

    # Register tools
    executor.register_tool(
        "search",
        "src/tools/r2e/search_func.py",
        execution_mode="k8s"
    )

    # Use async context manager for automatic cleanup
    async with executor:
        # Execute tool
        tool_calls = [{
            "tool": "search",
            "parameters": {"query": "example"}
        }]

        results = await executor.process_async(tool_calls)
        print(results)

asyncio.run(main())
```

### Example 3: Context Management

```python
from src.core.context_engineering_node import ContextEngineeringNode

# Create context manager
context = ContextEngineeringNode(
    name="MyContext",
    max_context_length=10
)

# Add messages
context.add_message(
    "You are a helpful assistant",
    message_role="system",
    message_type="system_prompt"
)

context.add_message(
    "What is AI?",
    message_role="user",
    message_type="query"
)

# Get LLM-ready context
llm_context = context.get_llm_context()
print(llm_context)

# Compress context if needed
context.compress_context(keep_first=1, keep_last=5)
```

### Example 4: Tool Parsing

```python
from src.core.tool_parsing_node import ToolParsingNode

# Create parser
parser = ToolParsingNode(name="ToolParser")

# Parse LLM response
llm_response = {
    "content": '''
    ```json
    {
        "tool": "search",
        "parameters": {"query": "example"}
    }
    ```
    '''
}

parsed_tools = parser.process(llm_response)
print(parsed_tools)
```

### Example 5: Timeline Tracking

```python
from src.core.timeline import get_timeline
from src.core.llm_node import LLMNode

# Get global timeline instance
timeline = get_timeline()

# Create nodes with timeline enabled
llm_node = LLMNode(name="LLM", timeline_enabled=True)

# Execute some operations
# ...

# View performance summary
timeline.print_summary(detailed=True)

# Export to JSON
timeline.export_json("timeline_report.json")

# Clear for next run
timeline.clear()
```

## Project Structure

```
agentic-rollout-library/
├── src/
│   ├── core/                    # Core node implementations
│   │   ├── base_node.py         # Abstract base class
│   │   ├── llm_node.py          # LLM integration
│   │   ├── tool_execution_node.py      # Tool execution
│   │   ├── k8s_tool_execution_node.py  # K8S tool execution
│   │   ├── context_engineering_node.py # Context management
│   │   ├── tool_parsing_node.py        # Tool call parsing
│   │   └── timeline.py          # Performance tracking
│   ├── tools/                   # Tool implementations
│   │   ├── base_tool.py         # Tool wrapper base
│   │   ├── r2e/                 # R2E tool collection
│   │   ├── miaoda/              # Miaoda tool collection
│   │   └── deepseek/            # DeepSeek tool collection
│   ├── utils/                   # Utility modules
│   │   ├── llm_api_utils.py     # LLM API helpers
│   │   └── bedrock_claude_handle.py  # AWS Bedrock integration
│   └── tests/                   # Unit tests
└── README.md
```

## Testing

The library includes comprehensive test coverage:

```bash
# Run all tests
python -m pytest src/tests/

# Run specific test file
python -m pytest src/tests/test_llm_node.py

# Run with coverage
python -m pytest --cov=src src/tests/
```

### Tool-specific Tests

Each tool collection has its own test suite:

```bash
# R2E tools
python src/tools/tests/r2e/run_all_tests.py

# Miaoda tools (requires K8S setup)
python src/tools/tests/miaoda/run_all_tests.py
```

## Advanced Features

### 1. Retry Configuration

```python
llm_node = LLMNode(name="LLM")
llm_node.set_retry_config(
    max_retries=5,
    initial_delay=2,
    max_delay=120,
    exponential_base=2
)
```

### 2. Custom Tool Parsers

```python
from src.core.tool_parsing_node import create_structured_parser

# Create OpenAI-specific parser
parser = ToolParsingNode(
    parse_function=create_structured_parser("openai")
)
```

### 3. K8S Pod Configuration

```python
executor = K8SToolExecutionNode(
    namespace="ml-workflows",
    image="your-registry/custom-image:latest",
    node_selector={"gpu": "true"},
    cpu_request="2",
    memory_request="4Gi",
    dns_policy="None",
    dns_config={
        "nameservers": ["8.8.8.8", "8.8.4.4"],
        "searches": ["default.svc.cluster.local"]
    }
)
```

### 4. Async Operations

All nodes support async execution:

```python
async def process_with_nodes():
    llm_response = await llm_node.process_with_timing(
        messages,
        event_type="llm_call"
    )

    parsed_tools = await parser.process_async(llm_response)
    results = await executor.process_async(parsed_tools)

    return results
```

## Configuration

### Environment Variables

The library supports the following environment variables:

```bash
# LLM API Configuration
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"

# AWS Bedrock Configuration
export BEDROCK_AK="your-access-key"
export BEDROCK_SK="your-secret-key"
export BEDROCK_REGION="us-west-2"

# Proxy Configuration (if needed)
export HTTP_PROXY="http://proxy-server:port"
export HTTPS_PROXY="http://proxy-server:port"
```

## Contributing

Contributions are welcome! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## License

[Add your license information here]

## Acknowledgments

This library uses the following open-source projects:
- [Kodo](https://github.com/your-kodo-repo) for Kubernetes operations
- Various LLM providers (OpenAI, Anthropic, DeepSeek, etc.)

## Support

For issues, questions, or contributions, please open an issue on GitHub.
