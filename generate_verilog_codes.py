import os
import json
from datetime import datetime
from pathlib import Path
import requests
from tqdm import tqdm
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =========================
# TASK GENERATION PROMPT
# =========================
PROMPT_TASK = f'''
**Context & Goal**:

{os.getenv("PROMPT_TASK_CONTEXT")}

**Requirements**:

{os.getenv("PROMPT_TASK_REQUIREMENTS")}
'''

# =========================
# Ollama API Client
# =========================
def generate_with_ollama(prompt, model, temperature=0.7):
    """Generate text using Ollama API with specified model."""
    ollama_url = os.getenv("OLLAMA_API_URL")
    url = f"{ollama_url}/api/generate"
    
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": temperature,
        "stream": False
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            return response.json().get("response", "")
        else:
            raise Exception(f"Ollama API returned status code {response.status_code}: {response.text}")
    except Exception as e:
        print(f"Error calling Ollama API: {e}")
        return None

# =========================
# JSON Sanitizer
# =========================
def sanitize_json(text):
    """Extract and clean JSON content from the model response."""
    print("Raw response from LLM:\n", text[:500] + "..." if len(text) > 500 else text)
    
    # Find JSON content between triple backticks if present
    import re
    json_pattern = r'```(?:json)?\s*([\s\S]*?)```'
    json_matches = re.findall(json_pattern, text)
    
    if json_matches:
        cleaned = json_matches[0].strip()
    else:
        # Try to find JSON-like content with curly braces
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            cleaned = text[brace_start:brace_end+1].strip()
        else:
            cleaned = text.strip()
    
    try:
        parsed_json = json.loads(cleaned)
        print(f"JSON structure type: {type(parsed_json)}")
        
        result_dict = {}
        
        # Handle different JSON structures
        if isinstance(parsed_json, dict):
            # If there's a "tasks" key with a list, process it specially
            if "tasks" in parsed_json and isinstance(parsed_json["tasks"], list):
                for i, task in enumerate(parsed_json["tasks"]):
                    task_id = str(i + 1)
                    # If the task is a dict with numeric keys, use those directly
                    if isinstance(task, dict) and any(key.isdigit() for key in task.keys()):
                        for key, value in task.items():
                            if key.isdigit():
                                result_dict[key] = value
                    else:
                        # Otherwise just use the task as is with a numeric key
                        result_dict[task_id] = task
            
            # If there are top-level numeric keys, use those
            elif any(key.isdigit() for key in parsed_json.keys()):
                for key, value in parsed_json.items():
                    if key.isdigit():
                        result_dict[key] = value
            
            # Otherwise, treat each key-value pair as a task
            else:
                for i, (key, value) in enumerate(parsed_json.items()):
                    result_dict[str(i + 1)] = f"# {key}\n\n{value}"
        
        # If we got a list instead of a dict, convert it
        elif isinstance(parsed_json, list):
            for i, item in enumerate(parsed_json):
                result_dict[str(i + 1)] = item
        
        # If we didn't find any valid tasks, create a fallback
        if not result_dict:
            result_dict = {"1": json.dumps(parsed_json, indent=2)}
        
        print(f"Processed JSON into {len(result_dict)} tasks")
        return result_dict
            
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")
        print(f"Extracted content:\n{cleaned}")
        
        # As a fallback, try to manually create tasks if JSON parsing fails
        fallback_tasks = {}
        try:
            # Split by task numbers if we can find them
            task_sections = re.split(r'Task\s+(\d+):', text)
            if len(task_sections) > 1:
                for i in range(1, len(task_sections), 2):
                    task_num = task_sections[i]
                    task_content = task_sections[i+1].strip() if i+1 < len(task_sections) else ""
                    if task_content:
                        fallback_tasks[task_num] = f"# Task {task_num}:{task_content}"
                
                if fallback_tasks:
                    print(f"Created {len(fallback_tasks)} tasks using fallback parser")
                    return fallback_tasks
        except Exception as inner_e:
            print(f"Fallback parsing also failed: {inner_e}")
        
        # If all parsing fails, create a single task with the response
        return {"1": text}

# =========================
# Save Task Files
# =========================
def save_tasks_to_files(task_dict):
    """Save each task to a separate markdown file."""
    tasks_dir = Path("DATA")
    tasks_dir.mkdir(exist_ok=True)

    today_str = datetime.today().strftime('%Y-%m-%d')
    saved_files = []

    for task_id, task_content in task_dict.items():
        try:
            # Parse the task_id to an integer, handling numeric strings
            task_num = int(task_id)
            filename = f"{today_str}_{task_num:03d}_task.md"
            filepath = tasks_dir / filename
            
            # Handle different types of task_content
            if isinstance(task_content, dict):
                # If task_content is a dict, convert it to markdown format
                content_to_write = json.dumps(task_content, indent=2)
            else:
                # If it's a string, use it directly
                content_to_write = task_content
                
            with open(filepath, 'w') as f:
                f.write(content_to_write.strip() if isinstance(content_to_write, str) else str(content_to_write))
                
            print(f"Saved: {filepath}")
            saved_files.append(filepath)
        except ValueError as e:
            print(f"Error saving task {task_id}: {e}")
            print(f"Task content type: {type(task_content)}")
            if task_id != "tasks":  # Skip the 'tasks' key itself
                try:
                    # Try with a fallback id
                    fallback_id = len(saved_files) + 1
                    filename = f"{today_str}_{fallback_id:03d}_task.md"
                    filepath = tasks_dir / filename
                    
                    with open(filepath, 'w') as f:
                        if isinstance(task_content, (dict, list)):
                            f.write(json.dumps(task_content, indent=2))
                        else:
                            f.write(str(task_content).strip())
                    
                    print(f"Saved with fallback ID: {filepath}")
                    saved_files.append(filepath)
                except Exception as inner_e:
                    print(f"Fallback save also failed: {inner_e}")
    return saved_files

# =========================
# Generate Tasks
# =========================
def generate_programming_tasks(prompt_task):
    """Generate tasks and return saved file paths."""
    print(f"Generating tasks using Ollama with {os.getenv('LLM_MODEL_TASK')} model...")
    try:
        response_text = generate_with_ollama(prompt_task, os.getenv('LLM_MODEL_TASK'))
        if not response_text:
            raise ValueError("Empty response from Ollama")
            
        print("Received response from Ollama. Processing JSON...")
        task_dict = sanitize_json(response_text)
        print(f"Successfully processed response. Contains {len(task_dict)} tasks.")
        saved_files = save_tasks_to_files(task_dict)
        return saved_files
    except Exception as e:
        print(f"Error generating tasks: {e}")
        return []

# =========================
# Generate Report for One Task
# =========================
def generate_report_for_task(task_file_path):
    """Generate a report for a given task file using Ollama."""
    with open(task_file_path, 'r') as f:
        task_content = f.read()

    prompt_code_template = os.getenv("PROMPT_CODE")
    prompt_code = prompt_code_template.format(task_content=task_content)

    try:
        response_text = generate_with_ollama(prompt_code, os.getenv('LLM_MODEL_SOLUTION'))
        if not response_text:
            raise ValueError("Empty response from Ollama")
        return response_text
    except Exception as e:
        print(f"Error generating report for {task_file_path}: {e}")
        return None

# =========================
# Process All Task Files
# =========================
def process_task_files(task_files):
    """Generate reports for given task files."""
    print("\nGenerating reports for each task...")
    for task_file in tqdm(task_files, desc="Processing Tasks", unit="file"):
        report_filename = str(task_file).replace("_task", "_output")
        report_content = generate_report_for_task(task_file)

        if report_content:
            # Clean the report content if needed (remove markdown code blocks, etc.)
            cleaned = report_content.strip()
            
            # Check for markdown code blocks and remove them if present
            if cleaned.startswith('```markdown'):
                cleaned = cleaned[len('```markdown'):].strip()
            if cleaned.endswith('```'):
                cleaned = cleaned[:-3].strip()

            with open(report_filename, 'w') as f:
                f.write(cleaned)
            tqdm.write(f"✔️  Generated report: {report_filename}")
        else:
            tqdm.write(f"❌ Failed to generate report for {task_file}")

# =========================
# Main Execution
# =========================
if __name__ == "__main__":
    # Step 1: Generate tasks and save them
    task_files = generate_programming_tasks(PROMPT_TASK)

    # Step 2: Generate reports for the saved task files
    if task_files:
        process_task_files(task_files)
    else:
        print("No tasks generated, skipping report generation.")