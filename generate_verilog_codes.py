import os
import json
import re
from datetime import datetime
from pathlib import Path
import requests
from tqdm import tqdm
from dotenv import load_dotenv
import logging
from typing import Dict, List, Union

# Load environment variables from .env file
load_dotenv()

# Configure logging to write to a file
log_filename = "generate_verilog_codes.log"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename=log_filename, filemode='a')

# =========================
# TASK GENERATION PROMPT
# =========================
PROMPT_TASK = os.getenv("PROMPT_TASK")
if not PROMPT_TASK:
    logging.error("PROMPT_TASK environment variable is not set.")
    exit(1)

# =========================
# Ollama API Client
# =========================
def generate_with_ollama(prompt: str, model: str, temperature: float = 0.7) -> Union[str, None]:
    """Generate text using Ollama API with specified model."""
    ollama_url = os.getenv("OLLAMA_API_URL")
    if not ollama_url:
        logging.error("OLLAMA_API_URL environment variable is not set.")
        return None

    url = f"{ollama_url}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": temperature,
        "stream": False
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("response", "")
    except requests.RequestException as e:
        logging.error(f"Error calling Ollama API: {e}")
        return None

# =========================
# JSON Sanitizer
# =========================
def sanitize_json(text: str) -> Dict[str, str]:
    """Extract and clean JSON content from the model response."""
    logging.info("Raw response from LLM:\n%s", text[:500] + "..." if len(text) > 500 else text)
 
    """Split response text into individual JSON blocks."""
    json_blocks = re.findall(r'```json\n(.*?)\n```', text, re.DOTALL)

    lines = json_blocks[0].splitlines()
    if lines[0].strip() == "```json" and lines[-1].strip() == "```":
        lines = lines[1:-1]
        cleaned = "\n".join(lines).strip()
    else:
        cleaned = text.strip()
    logging.info("Cleaned response:\n%s", cleaned[:500] + "..." if len(cleaned) > 500 else cleaned)
    
    try:
        parsed_json = json.loads(cleaned)
        logging.info(f"JSON structure type: {type(parsed_json)}")
        
        result_dict = {}
        if isinstance(parsed_json, dict):
            if any(key.isdigit() for key in parsed_json.keys()):
                for key, value in parsed_json.items():
                    if key.isdigit():
                        result_dict[key] = value
            else:
                for i, (key, value) in enumerate(parsed_json.items()):
                    result_dict[str(i + 1)] = f"# {key}\n\n{value}"
        elif isinstance(parsed_json, list):
            for i, item in enumerate(parsed_json):
                result_dict[str(i + 1)] = item
        
        if not result_dict:
            result_dict = {"1": json.dumps(parsed_json, indent=2)}
        
        logging.info(f"Processed JSON into {len(result_dict)} tasks")
        return result_dict
    except json.JSONDecodeError as e:
        logging.error(f"Failed to parse JSON: {e}")
        logging.error(f"Extracted content:\n{cleaned}")
        
        fallback_tasks = {}
        try:
            task_sections = re.split(r'Task\s+(\d+):', text)
            if len(task_sections) > 1:
                for i in range(1, len(task_sections), 2):
                    task_num = task_sections[i]
                    task_content = task_sections[i+1].strip() if i+1 < len(task_sections) else ""
                    if task_content:
                        fallback_tasks[task_num] = f"# Task {task_num}:{task_content}"
                
                if fallback_tasks:
                    logging.info(f"Created {len(fallback_tasks)} tasks using fallback parser")
                    return fallback_tasks
        except Exception as inner_e:
            logging.error(f"Fallback parsing also failed: {inner_e}")
        
        return {"1": text}

# =========================
# Save Task Files
# =========================
def save_tasks_to_files(task_dict: Dict[str, Union[str, dict]]) -> List[Path]:
    """Save each task to a separate markdown file."""
    tasks_dir = Path("DATA")
    tasks_dir.mkdir(exist_ok=True)

    today_str = datetime.today().strftime('%Y-%m-%d')
    saved_files = []

    for task_id, task_content in task_dict.items():
        try:
            task_num = int(task_id)
            filename = f"{today_str}_{task_num:03d}_task.md"
            filepath = tasks_dir / filename
            
            content_to_write = json.dumps(task_content, indent=2) if isinstance(task_content, dict) else task_content
            with open(filepath, 'w') as f:
                f.write(content_to_write.strip() if isinstance(content_to_write, str) else str(content_to_write))
                
            logging.info(f"Saved: {filepath}")
            saved_files.append(filepath)
        except ValueError as e:
            logging.error(f"Error saving task {task_id}: {e}")
            if task_id != "tasks":
                try:
                    fallback_id = len(saved_files) + 1
                    filename = f"{today_str}_{fallback_id:03d}_task.md"
                    filepath = tasks_dir / filename
                    
                    with open(filepath, 'w') as f:
                        if isinstance(task_content, (dict, list)):
                            f.write(json.dumps(task_content, indent=2))
                        else:
                            f.write(str(task_content).strip())
                    
                    logging.info(f"Saved with fallback ID: {filepath}")
                    saved_files.append(filepath)
                except Exception as inner_e:
                    logging.error(f"Fallback save also failed: {inner_e}")
    return saved_files

# =========================
# Generate Tasks
# =========================
def generate_programming_tasks(prompt_task: str) -> List[Path]:
    """Generate tasks and return saved file paths."""
    logging.info(f"Generating tasks using Ollama with {os.getenv('LLM_MODEL_TASK')} model...")
    try:
        response_text = generate_with_ollama(prompt_task, os.getenv('LLM_MODEL_TASK'))
        if not response_text:
            raise ValueError("Empty response from Ollama")
            
        logging.info("Received response from Ollama. Processing JSON...")
        task_dict = sanitize_json(response_text)
        logging.info(f"Successfully processed response. Contains {len(task_dict)} tasks.")
        saved_files = save_tasks_to_files(task_dict)
        return saved_files
    except Exception as e:
        logging.error(f"Error generating tasks: {e}")
        return []

# =========================
# Generate Report for One Task
# =========================
def generate_report_for_task(task_file_path: Path) -> Union[str, None]:
    """Generate a report for a given task file using Ollama."""
    with open(task_file_path, 'r') as f:
        task_content = f.read()

    prompt_code_template = os.getenv("PROMPT_CODE")
    if not prompt_code_template:
        logging.error("PROMPT_CODE environment variable is not set.")
        return None

    prompt_code = prompt_code_template.format(task_content=task_content)

    try:
        response_text = generate_with_ollama(prompt_code, os.getenv('LLM_MODEL_SOLUTION'))
        if not response_text:
            raise ValueError("Empty response from Ollama")
        return response_text
    except Exception as e:
        logging.error(f"Error generating report for {task_file_path}: {e}")
        return None

# =========================
# Process All Task Files
# =========================
def process_task_files(task_files: List[Path]) -> None:
    """Generate reports for given task files."""
    logging.info("\nGenerating reports for each task...")
    for task_file in tqdm(task_files, desc="Processing Tasks", unit="file"):
        report_filename = str(task_file).replace("_task", "_output")
        report_content = generate_report_for_task(task_file)

        if report_content:
            cleaned = report_content.strip()
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
    task_files = generate_programming_tasks(PROMPT_TASK)
    if task_files:
        process_task_files(task_files)
    else:
        logging.info("No tasks generated, skipping report generation.")