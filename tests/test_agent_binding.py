from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from src.core.llm import LLMFactory
from src.core.registry import ToolRegistry
# Import tools to ensure registration
import src.tools.system_stats 

# Load Environment Variables
load_dotenv()

def test_tool_binding():
    print("--- Agent Integration Test: Tool Binding (Local Llama 3.1) ---")

    # 1. Initialize Brain (Local Llama 3.1)
    try:
        llm = LLMFactory.get_model(model_name="llama3.1:latest")
    except Exception as e:
        print(f"FATAL: Failed to initialize LLM. {e}")
        return

    # 2. Fetch Hands (Tools)
    tools = ToolRegistry.get_tools()
    print(f"Loaded Tools: {[t.name for t in tools]}")

    # 3. Bind Tools to LLM
    llm_with_tools = llm.bind_tools(tools)

    # 4. Simulate a User Query
    user_query = "Can you check if the system memory is healthy right now?"
    print(f"\nUser Query: '{user_query}'")

    # 5. Invoke LLM
    try:
        response = llm_with_tools.invoke([HumanMessage(content=user_query)])
        
        # 6. Verify Decision
        print("\n--- LLM Decision ---")
        print(f"Content: {response.content}")
        
        if response.tool_calls:
            print(f"Tool Call Detected: {response.tool_calls}")
            if response.tool_calls[0]['name'] == 'check_system_health':
                print("SUCCESS: The Agent decided to use the correct tool.")
            else:
                print("FAILURE: Agent chose the wrong tool.")
        else:
            print("FAILURE: Agent did not call any tool. (Pure text response)")
            
    except Exception as e:
        print(f"ERROR: Error during invocation: {e}")

if __name__ == "__main__":
    test_tool_binding()