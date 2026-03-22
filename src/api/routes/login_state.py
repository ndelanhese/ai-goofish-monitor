"""
Login state management routes
"""
import os
import json
import aiofiles
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/api/login-state", tags=["login-state"])


class LoginStateUpdate(BaseModel):
    """Login state update model"""
    content: str


@router.post("", response_model=dict)
async def update_login_state(
    data: LoginStateUpdate,
):
    """Receive the login state JSON string from the frontend and save it to xianyu_state.json"""
    state_file = "xianyu_state.json"

    try:
        # Validate that the content is valid JSON
        json.loads(data.content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="The provided content is not valid JSON.")

    try:
        async with aiofiles.open(state_file, 'w', encoding='utf-8') as f:
            await f.write(data.content)
        return {"message": f"Login state file '{state_file}' updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error writing login state file: {e}")


@router.delete("", response_model=dict)
async def delete_login_state():
    """Delete the xianyu_state.json file"""
    state_file = "xianyu_state.json"

    if os.path.exists(state_file):
        try:
            os.remove(state_file)
            return {"message": "Login state file deleted successfully."}
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Error deleting login state file: {e}")

    return {"message": "Login state file does not exist; nothing to delete."}
