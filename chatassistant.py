from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from fastapi.responses import FileResponse
from chatsessiondata import ChatSessionData
from configmanager import conf, log
import configmanager


@asynccontextmanager
async def lifespan( app: FastAPI):
    log.info("Webserver starting up...")
    
    app.state.dbpool = await configmanager.init_db()
    ( app.state.mcptools, app.state.toolmap ) = await configmanager.get_tools()
    yield
    log.info("Webserver shutting down...")
    app.state.dbpool.close()
    await app.state.dbpool.wait_closed()


app = FastAPI( lifespan=lifespan )


async def call_mcp_tool(name: str, args: dict):    
    url = configmanager.get_server_for_tool( app.state.toolmap, name)
    try:
        async with sse_client(url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                        
                result = await session.call_tool( name, arguments=args )
                            
                if result.content and hasattr(result.content[0], 'text'):
                    return result.content[0].text # type: ignore
                return str(result.content)
    except Exception as e:
        log.error(f"Tool not available: {e}")
    return f"Tool not found: '{name}'"



@app.get("/")
async def get():
    return FileResponse("www/index.html")


@app.get("/{file_path:path}")
async def get_static(file_path: str):
    return FileResponse(f"www/{file_path}")



@app.post("/api/v1/createChat")
async def initChatSession():
    session_data = ChatSessionData( app )
    sid = await session_data.createChat("default") # type: ignore
    log.info( f"Chat created: {sid}" )
    return {"sid": sid}


@app.websocket("/ws/{sid}")
async def websocket_endpoint(websocket: WebSocket, sid: str):

    title_generation = False
    async def generate_title(llm, session_data, websocket):
        nonlocal title_generation 
        title_generation = True
        msgs = ""
        for msg in session_data.messages:
            if isinstance(msg, HumanMessage):
                msgs = msgs + "\n\n" + msg.content # type: ignore
        prompt = f"Generate a maximum 5 word title for this conversation: '{msgs}'. Return only the title!"
        
        res = await llm.ainvoke([("human", prompt)])
        new_title = res.content.strip().replace('"', '')
        
        session_data.name = new_title
        await session_data.saveChat()
        await websocket.send_json({"type": "title", "content": new_title})
        title_generation = False
        return new_title


    await websocket.accept()

    try:
        session_data = ChatSessionData( app )
        await session_data.load(sid)
    except ValueError as e:
        await websocket.send_json({"type": "error", "content": str(e)})
        await websocket.close()
        return

    llm = ChatGoogleGenerativeAI(
        model = session_data.settings["model"],
        google_api_key=session_data.settings["modelConfig"]['apikey']
    )
    llm_with_tools = llm.bind_tools( session_data.tools )

    messages = await session_data.getLastMessages(35)
    messages.insert( 0, SystemMessage( content=session_data.settings["systemprompt"] ) )
    try:
        while True:
            if ( len(session_data.messages) > 4 and session_data.name == "" and not title_generation ):
                await generate_title( llm, session_data, websocket )
                
            try:
                if len( session_data.messages) > 1:
                    while len(messages) > 25 or not isinstance(messages[1], HumanMessage):
                        messages.pop(1)  # Az első üzenet a rendszerüzenet, azt nem töröljük
                        if len(messages) <= 1:
                            break

                user_text = await websocket.receive_text()
                messages.append( HumanMessage(content=user_text) ) 
                await session_data.addMessage( HumanMessage(content=user_text) )

                toolCalls = 0
                while True:
                    toolCalls += 1
                    if toolCalls > 15:
                        await websocket.send_json({"type": "error", "content": "Túl sok eszköz hívás egy kérdésre."})
                        break
                    ai_msg = await llm_with_tools.ainvoke( messages )
                    messages.append(ai_msg)
                    await session_data.addMessage( ai_msg )

                    if not ai_msg.tool_calls:
                        # Ha nincs több tool hívás, kilépünk a belső loopból
                        await websocket.send_json({"type": "token", "content": ai_msg.content})
                        await websocket.send_json({"type": "done"})
                        break
                    
                    # Ha vannak tool hívások, mindegyiket végrehajtjuk
                    for tool_call in ai_msg.tool_calls:
                        await websocket.send_json({"type": "token", "content": f"*{tool_call['name']} hívása...* "})
                        await websocket.send_json({"type": "done"})
                        
                        # MCP hívás
                        observation = await call_mcp_tool(tool_call["name"], tool_call["args"])
                        
                        # Visszajelzés a történetbe
                        messages.append( ToolMessage( content=str(observation), tool_call_id=tool_call["id"] ) )
                        await session_data.addMessage( ToolMessage( content=str(observation), tool_call_id=tool_call["id"] ) )

            except Exception as e:
                await websocket.send_json({"type": "error", "content": str(e)})
                break

    except Exception as e:
        print( "Session end:", str(e) )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=conf.get("webserver.listenaddress", "0.0.0.0"), port=conf.get("webserver.port" ) ) # type: ignore