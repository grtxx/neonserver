from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket # type: ignore
from fastapi.responses import FileResponse # type: ignore
from configmanager import conf, log
import configmanager
from fastapi import Request # type: ignore
from aichat import AIChat


@asynccontextmanager
async def lifespan( app: FastAPI):
    log.info("NEON starting up...")
    
    app.state.dbpool = await configmanager.init_db()
    ( app.state.mcptools, app.state.toolmap ) = await configmanager.get_tools()
    yield
    log.info("NEON shutting down...")
    app.state.dbpool.close()
    await app.state.dbpool.wait_closed()


app = FastAPI( lifespan=lifespan )


@app.get("/")
async def get():
    return FileResponse("www/index.html")


@app.get("/api/v1/chat/{sid}/history")
async def get_chat_history(sid: str):
    chat = AIChat( app )
    await chat.loadChat( sid )
    messages = []
    async for msg in chat.getHistory():
        async for cmd in chat.session_data.extractCommands( msg.content, 0, exec=False ):
            if ( cmd["type"] == "result" ):
                content = cmd["content"]
                break
        if ( msg.type != "tool" ):
            messages.append( {
                "type": msg.type,
                "content": content,
                "tool_call_id": getattr(msg, "tool_call_id", None)
            } )
    return {
        "sid": chat.getSid(),
        "name": chat.getName(),
        "messages": messages
    }


@app.get("/{file_path:path}")
async def get_static(file_path: str):
    return FileResponse(f"www/{file_path}")


@app.post("/api/v1/createchat")
async def createChat( request: Request):
    try:
        post_data = (await request.json())[0]
    except Exception as e:
        post_data = {}

    cht = AIChat( app )
    await cht.createChat( post_data["personality"] if "personality" in post_data else "default" )
    cht.setUserdata( post_data["userdata"] if "userdata" in post_data else {} )
    cht.setCredentials( post_data["credentials"] if "credentials" in post_data else {} )
    await cht.save()
    try:
        log.info( f"Chat created: {cht.getSid()} for {cht.session_data.settings['userdata']['fullname']}" )
    except:
        pass
    return { "sid": cht.getSid() }



@app.websocket("/ws/{sid}")
async def websocket_endpoint(websocket: WebSocket, sid: str):

    async def approveCallback( req ):
        await websocket.send_json( { "type": 'approverequest', "content": req } )
        result = await websocket.receive_text()
        return result;


    chat = AIChat( app, 
                  onApproveCallback=approveCallback,
                  activeHistorySize=int( conf.get("chatparams.previousmessages", 15) ) # type: ignore
    ) # type: ignore
    await chat.loadChat( sid )

    await websocket.accept()
    try:
        while True:               
            user_text = await websocket.receive_text()
            if ( user_text == "" ):
                continue
            async for msg in chat.Question( user_text ):
                await websocket.send_json( msg )

    except Exception as e:
        log.error( f"Websocket error: {str(e)}" )
    finally:
        await websocket.close()



if __name__ == "__main__":
    import uvicorn # type: ignore
    uvicorn.run(app, host=conf.get("webserver.listenaddress", "0.0.0.0"), port=conf.get("webserver.port" ) ) # type: ignore