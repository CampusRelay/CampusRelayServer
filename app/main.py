from app.routers import classroom_members
from app.routers import classroom_join_requests
from app.routers import messages
from app.routers import devices
from app.routers import classroom_requests
from app.routers import teacher_registration

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, health, schools, school_join_codes
from app.routers import device_websocket

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
app.include_router(auth.router)
app.include_router(schools.router)


@app.get("/")
def root():
    return {
        "name": "CampusRelay",
        "message": "CampusRelay API is running",
    }

app.include_router(teacher_registration.router)

app.include_router(school_join_codes.router)

app.include_router(classroom_requests.router)

app.include_router(devices.router)

app.include_router(messages.router)

app.include_router(device_websocket.router)

app.include_router(classroom_members.router)
app.include_router(classroom_join_requests.router)
