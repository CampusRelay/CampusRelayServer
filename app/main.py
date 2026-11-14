
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import health, schools

app = FastAPI(
    title="CampusRelay API",
    description="CampusRelay Backend Service",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(schools.router)


@app.get("/")
def root():
    return {
        "name": "CampusRelay",
        "message": "CampusRelay API is running",
    }
