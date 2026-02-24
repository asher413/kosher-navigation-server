from fastapi import FastAPI
from pydantic import BaseModel
import requests

app = FastAPI()

class RouteRequest(BaseModel):
    start: str
    end: str

@app.get("/")
def home():
    return {"status": "Server is running"}

@app.post("/route")
def calculate_route(data: RouteRequest):
    url = f"https://nominatim.openstreetmap.org/search"
    
    start_resp = requests.get(url, params={"q": data.start, "format": "json"}).json()
    end_resp = requests.get(url, params={"q": data.end, "format": "json"}).json()

    if not start_resp or not end_resp:
        return {"error": "Address not found"}

    return {
        "message": f"Route calculated from {data.start} to {data.end}",
        "note": "Routing engine can be expanded later"
    }
