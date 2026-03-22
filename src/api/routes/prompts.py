"""
Prompt management routes
"""
import os
import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/api/prompts", tags=["prompts"])


class PromptUpdate(BaseModel):
    """Prompt update model"""
    content: str


@router.get("")
async def list_prompts():
    """List all prompt files"""
    prompts_dir = "prompts"
    if not os.path.isdir(prompts_dir):
        return []
    return [f for f in os.listdir(prompts_dir) if f.endswith(".txt")]


@router.get("/{filename}")
async def get_prompt(filename: str):
    """Get the content of a prompt file"""
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = os.path.join("prompts", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Prompt file not found")

    async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
        content = await f.read()
    return {"filename": filename, "content": content}


@router.put("/{filename}")
async def update_prompt(
    filename: str,
    prompt_update: PromptUpdate,
):
    """Update the content of a prompt file"""
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = os.path.join("prompts", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Prompt file not found")

    try:
        async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
            await f.write(prompt_update.content)
        return {"message": f"Prompt file '{filename}' updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error writing file: {e}")
