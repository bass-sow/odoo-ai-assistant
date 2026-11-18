"""AWS Lambda entry point. Mangum translates Lambda events into ASGI requests."""

from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="auto")
