from starlette.middleware.cors import CORSMiddleware

import api


def test_development_cors_allows_localhost_and_loopback_frontend_origins():
    cors = next(
        middleware
        for middleware in api.app.user_middleware
        if middleware.cls is CORSMiddleware
    )

    origins = set(cors.kwargs["allow_origins"])
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins
    assert "http://localhost:5173" in origins
    assert "http://127.0.0.1:5173" in origins
