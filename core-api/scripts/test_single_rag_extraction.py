import asyncio
import os
import sys
import json
from pydantic import BaseModel, Field

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from build_rag_dataset import process_task_with_gemini, clean_html
from openai import AsyncOpenAI

async def test_extraction():
    # Настройки подключения к LiteLLM Proxy
    litellm_key = os.getenv("LITELLM_API_KEY", "sk-intraservice-master-key")
    litellm_base_url = os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1")
    
    # Инициализация OpenAI Client
    llm_client = AsyncOpenAI(api_key=litellm_key, base_url=litellm_base_url)
    
    # Загружаем сохраненный файл
    data_file = "/app/scripts/task_132437_data.json"
    if not os.path.exists(data_file):
        print(f"Error: {data_file} not found")
        return
        
    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    task = data["task"]["Task"]
    
    # comments.TaskLifetimes
    comments = data["comments"]["TaskLifetimes"]
    
    print("Running process_task_with_gemini...")
    kb_entry = await process_task_with_gemini(llm_client, task, comments)
    
    if kb_entry:
        print("\n=== Extracted RAG Entry ===")
        print(f"Problem: {kb_entry.problem}")
        print(f"Solution: {kb_entry.solution}")
        print(f"Classification: {kb_entry.classification}")
        print("===========================")
    else:
        print("Error: extraction returned None")

if __name__ == "__main__":
    asyncio.run(test_extraction())
