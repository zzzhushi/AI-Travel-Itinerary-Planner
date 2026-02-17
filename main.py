

import os
import asyncio
from typing import Optional, List
from unittest import runner
from google.adk.agents import Agent, SequentialAgent, ParallelAgent, LoopAgent
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.tools import AgentTool, FunctionTool, google_search
from google.genai import types


async def main(argv: Optional[List[str]] = None) -> None:
    print("helloworld")

    # Safely read API key from environment (avoid relying on unavailable UserSecretsClient)
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    if GOOGLE_API_KEY:
        os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
        print("✅ Gemini API key setup complete.")
    else:
        print("🔑 Authentication Warning: 'GOOGLE_API_KEY' not found in environment variables.")

    retry_config=types.HttpRetryOptions(
        attempts=5,  # Maximum retry attempts
        exp_base=7,  # Delay multiplier
        initial_delay=1,
        http_status_codes=[429, 500, 503, 504], # Retry on these HTTP errors
    )

    # Research Agent: Its job is to use the google_search tool and present findings.
    research_agent = Agent(
        name="ResearchAgent",
        model=Gemini(
            model="gemini-2.5-flash-lite",
            retry_options=retry_config
        ),
        instruction="""You are a specialized research travel agent. Your only job is to use the
        google_search tool to find 2-3 pieces of relevant information on the given topic and present the findings with citations.""",
        tools=[google_search],
        output_key="research_findings",  # The result of this agent will be stored in the session state with this key.
    )

    summarizer_agent = Agent(
        name="SummarizerAgent",
        model=Gemini(
            model="gemini-2.5-flash-lite",
            retry_options=retry_config
        ),
        # The instruction is modified to request a bulleted list for a clear output format.
        instruction="""Read the provided research findings: {research_findings}
    Create a concise summary as a bulleted list with 3-5 key points.""",
        output_key="final_summary",
    )

    root_agent = Agent(
        name="ResearchCoordinator",
        model=Gemini(
            model="gemini-2.5-flash-lite",
            retry_options=retry_config
        ),
        # This instruction tells the root agent HOW to use its tools (which are the other agents).
        instruction="""You are a research coordinator. Your goal is to answer the user's query by orchestrating a workflow.
    1. First, you MUST call the `ResearchAgent` tool to find relevant information on the topic provided by the user.
    2. Next, after receiving the research findings, you MUST call the `SummarizerAgent` tool to create a concise summary.
    3. Finally, present the final summary clearly to the user as your response.""",
        # We wrap the sub-agents in `AgentTool` to make them callable tools for the root agent.
        tools=[AgentTool(research_agent), AgentTool(summarizer_agent)],
    )

    runner = InMemoryRunner(agent=root_agent)
    response = await runner.run_debug(
        "I want to find information about the top travel destinations in Japan for cherry blossom season."
    )

    # print("Response:", response)


# if __name__ == "__main__":
#     asyncio.run(main())