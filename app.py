"""
app.py - FastAPI server.

    uvicorn app:app --reload            (or:  python app.py)
    http://127.0.0.1:8000/ui            web workbench (farmer chat + admin + evaluation + WhatsApp simulator)
    http://127.0.0.1:8000/docs          OpenAPI documentation of every endpoint

Endpoints required by IA Activity-2: /health  /query/text  /query/voice  /admin/documents  /admin/approve  /metrics  /ui
plus /eval/run, /demo/presets, /feedback, /admin/review, /webhook/whatsapp, /simulate/whatsapp ... (see handlers.ROUTES).
All endpoint logic is in handlers.py so the same behaviour is available from lite_server.py when FastAPI is not installed.
"""
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

import handlers

app = FastAPI(title="Voice-First Multilingual Agricultural Assistant", version=handlers.VERSION,
              description="Hindi / Marathi voice + text assistant with grounded citations, live mandi & weather tools, "
                          "a confidence gate and an evaluation harness. IA Activity-2 prototype.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _make_endpoint(route):
    async def endpoint(request: Request):
        body = await request.body()
        req = handlers.Req(method=request.method, path=request.url.path, query=dict(request.query_params),
                           headers={k: v for k, v in request.headers.items()}, body=body)
        status, payload, ctype = handlers.dispatch(req)
        if isinstance(payload, (bytes, bytearray)):
            return Response(content=bytes(payload), status_code=status, media_type=ctype.split(";")[0],
                            headers={"Content-Type": ctype})
        return JSONResponse(payload, status_code=status)
    endpoint.__name__ = route["fn"].__name__ + "_" + route["method"].lower()
    return endpoint


for _r in handlers.ROUTES:
    app.add_api_route(_r["path"], _make_endpoint(_r), methods=[_r["method"]], summary=_r["summary"],
                      include_in_schema=_r["path"] not in ("/",), name=_r["fn"].__name__ + "_" + _r["method"].lower())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")), reload=False)
