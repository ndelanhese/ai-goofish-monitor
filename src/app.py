"""
Main application entry point for the new architecture.
Integrates all routes and services.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.routes import (
    dashboard,
    tasks,
    logs,
    settings,
    prompts,
    results,
    login_state,
    websocket,
    accounts,
)
from src.api.dependencies import (
    set_process_service,
    set_scheduler_service,
    set_task_generation_service,
)
from src.services.task_service import TaskService
from src.services.process_service import ProcessService
from src.services.scheduler_service import SchedulerService
from src.services.task_log_cleanup_service import cleanup_task_logs
from src.services.task_generation_service import TaskGenerationService
from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.infrastructure.config.settings import settings as app_settings


# Global service instances
process_service = ProcessService()
scheduler_service = SchedulerService(process_service)
task_generation_service = TaskGenerationService()


async def _sync_task_runtime_status(task_id: int, is_running: bool) -> None:
    task_service = TaskService(SqliteTaskRepository())
    task = await task_service.get_task(task_id)
    if not task or task.is_running == is_running:
        return
    await task_service.update_task_status(task_id, is_running)
    await websocket.broadcast_message(
        "task_status_changed",
        {"id": task_id, "is_running": is_running},
    )


process_service.set_lifecycle_hooks(
    on_started=lambda task_id: _sync_task_runtime_status(task_id, True),
    on_stopped=lambda task_id: _sync_task_runtime_status(task_id, False),
)

# Set global ProcessService instance for dependency injection
set_process_service(process_service)
set_scheduler_service(scheduler_service)
set_task_generation_service(task_generation_service)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # On startup
    print("Starting application...")
    bootstrap_sqlite_storage()
    cleanup_task_logs(keep_days=app_settings.task_log_retention_days)

    # Reset all task statuses to stopped
    task_repo = SqliteTaskRepository()
    task_service = TaskService(task_repo)
    tasks_list = await task_service.get_all_tasks()

    for task in tasks_list:
        if task.is_running:
            await task_service.update_task_status(task.id, False)

    # Load scheduled jobs
    await scheduler_service.reload_jobs(tasks_list)
    scheduler_service.start()

    print("Application started")

    yield

    # On shutdown
    print("Shutting down application...")
    scheduler_service.stop()
    await process_service.stop_all()
    print("Application shut down")


# Create FastAPI application
app = FastAPI(
    title="Goofish Intelligent Monitor",
    description="AI-based Goofish product monitoring system",
    version="2.0.0",
    lifespan=lifespan
)

# Register routes
app.include_router(tasks.router)
app.include_router(dashboard.router)
app.include_router(logs.router)
app.include_router(settings.router)
app.include_router(prompts.router)
app.include_router(results.router)
app.include_router(login_state.router)
app.include_router(websocket.router)
app.include_router(accounts.router)

# Mount static files
# Legacy static files directory (for screenshots etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mount Vue 3 frontend build output
# Note: must be mounted after all API routes to avoid overriding them
import os
if os.path.exists("dist"):
    app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check (no authentication required)"""
    return {"status": "healthy", "message": "Service is running"}


# Authentication status check endpoint
from fastapi import Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/status")
async def auth_status(payload: LoginRequest):
    """Check authentication status"""
    if payload.username == app_settings.web_username and payload.password == app_settings.web_password:
        return {"authenticated": True, "username": payload.username}
    raise HTTPException(status_code=401, detail="Authentication failed")


# Root route - serve Vue 3 SPA
from fastapi.responses import JSONResponse

@app.get("/")
async def read_root(request: Request):
    """Serve the main page of the Vue 3 SPA"""
    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Frontend build artifacts not found. Please run: cd web-ui && npm run build"}
        )


# Catch-all route - handles all frontend routes (must be last)
@app.get("/{full_path:path}")
async def serve_spa(request: Request, full_path: str):
    """
    Catch-all route that redirects all non-API requests to index.html.
    This enables Vue Router HTML5 History mode support.
    """
    # If the request is for a static asset (e.g. favicon.ico), return 404
    if full_path.endswith(('.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.css', '.js', '.json')):
        return JSONResponse(status_code=404, content={"error": "Resource not found"})

    # All other paths return index.html for frontend routing
    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Frontend build artifacts not found. Please run: cd web-ui && npm run build"}
        )


if __name__ == "__main__":
    import uvicorn
    from src.infrastructure.config.settings import settings

    print(f"Starting application on port: {app_settings.server_port}")
    uvicorn.run(app, host="0.0.0.0", port=app_settings.server_port)
