from contextlib import asynccontextmanager
import json
from fastapi import FastAPI, WebSocket # type: ignore
import langchain
from mcp import ClientSession, StdioServerParameters # type: ignore
from mcp.client.sse import sse_client # type: ignore
from langchain_google_genai import ChatGoogleGenerativeAI # type: ignore
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage, SystemMessage # type: ignore
from fastapi.responses import FileResponse # type: ignore
from chatsessiondata import ChatSessionData
from configmanager import conf, log
import configmanager
from fastapi import Request # type: ignore


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


def normalize_params( args: dict, toolparams: dict ) -> dict:
    finalArguments = args
    if ( '__arg1' in args and len(args) == 1 and len(toolparams['inputSchema']['properties']) > 1 ):
        try:
            if isinstance(args['__arg1'], dict):
                args = args['__arg1']
            else:
                oargs = args
                args = json.loads( args['__arg1'] )
                if ( not isinstance(args, dict) ):
                    args = oargs
        except:
            pass
    if ( '__arg1' in args ):
        cnt = 0
        args2 = {}
        for k in toolparams['inputSchema']['properties'].keys():
            cnt = cnt + 1
            if ( ( '__arg%d' % cnt ) in args ):
                args2[ k ] = args[ '__arg%d' % cnt ]
        finalArguments = args2
    return finalArguments


async def call_mcp_tool(name: str, args: dict, websocket: WebSocket, progress=None ):    
    toolparams = configmanager.get_params_for_tool( app.state.toolmap, name)
    try:
        async with sse_client( toolparams['url'] ) as (read, write):
            async with ClientSession(read, write) as session:
                args = normalize_params( args, toolparams )
                while True:
                    await session.initialize()                
                    #if ( progress is not None ):
                    #    finalArguments['__progresscallback'] = progress
                    result = await session.call_tool( name, arguments=args )
                    if not "approvalToken" in toolparams['inputSchema']['properties']:
                        break
                    try:
                        decresult = json.loads( result.content[0].text )
                        approvalToken = decresult.get( "approvalToken", "" )
                        decresult["approvalToken"] = None
                    except:
                        # not a json response so not approval request
                        break
                    if ( not 'action' in decresult ):
                        # no approval needed so exit from the loop
                        break
                    if decresult['action'] == 'approvalrequest':
                        # automatically approve for now
                        await websocket.send_json({"type": "approvalrequest", "content": decresult })
                        useranswer = await websocket.receive_text()
                        try:
                            useranswer = json.loads( useranswer )
                        except:
                            # approval result invalid, break the loop
                            break
                        if ( not ( "type" in useranswer and "action" in useranswer ) ):
                            # invalid response, break the loop
                            break;
                        if ( useranswer["type"] == "approvalresponse" and useranswer["action"] == "approved" ):
                            # inject new params and call again
                            if "overrides" in useranswer:
                                for k in toolparams['inputSchema']['properties'].keys():
                                    if ( k in useranswer["overrides"] ):
                                        args[ k ] = useranswer["overrides"][ k ]
                            args["approvalToken"] = approvalToken
                        else:
                            # not approved, exit
                            result.content = useranswer["content"] if "content" in useranswer else "Tool operation interrupted by the user. DO NOT CALL ANY TOOL, WAIT FOR USER INPUT!" # type: ignore
                            break

                if result.content and hasattr(result.content[0], 'text'):
                    return result.content[0].text # type: ignore
                return str(result.content)
                                
    except Exception as e:
        log.error(f"Tool not available: {e}")
    return f"Tool not found: '{name}'"



@app.get("/")
async def get():
    return FileResponse("www/index.html")


@app.get("/api/v1/chat/{sid}/history")
async def get_chat_history(sid: str):
    session_data = ChatSessionData( app )
    await session_data.load(sid)
    messages = []
    async for msg in session_data.getHistory():
        messages.append( {
            "type": msg.type,
            "content": msg.content,
            "tool_call_id": getattr(msg, "tool_call_id", None)
        } )
    return {
        "sid": session_data.sid,
        "name": session_data.name,
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
    session_data = ChatSessionData( app )
    sid = await session_data.createChat( post_data["personality"] if "personality" in post_data else "default" )
    session_data.settings["userdata"] = post_data["userdata"] if "userdata" in post_data else {}
    session_data.settings["credentials"] = post_data["credentials"] if "credentials" in post_data else {}
    await session_data.save()
    try:
        log.info( f"Chat created: {sid} for {session_data.settings['userdata']['fullname']}" )
    except:
        pass
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
        prompt = f"Generate a maximum 8 word title for this conversation:\n\n'{msgs}'\n\nReturn only the title in the conversation's language!"
        
        res = await llm.ainvoke([("human", prompt)])
        new_title = res.content[0]["text"].strip()
        
        session_data.name = new_title
        await session_data.save()
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
    messages.insert( 0, SystemMessage( content=session_data.getCustomisedSystemPrompt() ) )
    try:
        while True:
            if ( len(session_data.messages) > 8 and session_data.name == "" and not title_generation ):
                await generate_title( llm, session_data, websocket )
                
            try:
                if len( session_data.messages) > 1:
                    while len(messages) > 25 or not isinstance(messages[1], HumanMessage):
                        messages.pop(1)  # Az első üzenet a rendszerüzenet, azt nem töröljük
                        if len(messages) <= 1:
                            break

                try:
                    user_text = await websocket.receive_text()
                    user_msg = HumanMessage( content=user_text )
                    await session_data.addMessage( user_msg )
                    messages.append( user_msg )
                    print( f"User input received: {user_text}" )
                except Exception as e:
                    break

                toolCalls = 0
                while True:
                    toolCalls += 1
                    if toolCalls > 15:
                        await websocket.send_json({"type": "token", "content": "** Túl sok eszköz hívás egy kérdésre. **"})
                        break
                    #ai_msg = await llm_with_tools.ainvoke( messages )

                    ai_msg = None
                    tool_calls_in_answer = []
                    async for event in llm_with_tools.astream_events(messages, version="v2"):
                        kind = event["event"]

                        if kind == "on_chat_model_stream":
                            chunk = event["data"]["chunk"] # type: ignore
                            if hasattr( chunk, "tool_calls"):
                                for tool_call in chunk.tool_calls: # type: ignore
                                    tool_calls_in_answer.append(tool_call)

                            if ai_msg is None:
                                ai_msg = chunk
                            else:
                                ai_msg += chunk
                            content = chunk.content # type: ignore
                            if content:
                                await websocket.send_json({"type": "token", "content": content } )

                        elif kind == "on_tool_start":
                            tool_name = event["name"]
                            try:
                                await websocket.send_json({"type": "token", "content": f"*{tool_name} hívása...*"})
                                await websocket.send_json({"type": "done"})
                            except:
                                pass
                            break
                        elif kind == "on_tool_end":
                            pass

                    if ai_msg:
                        await websocket.send_json({"type": "done"})
                        messages.append( ai_msg )
                        await session_data.addMessage( ai_msg )
                

                    if ( websocket.client_state.name != "CONNECTED" ):
                        raise Exception("Websocket disconnected")

                    if 'tool_calls' in ai_msg and ai_msg.tool_calls: # type: ignore
                        for tool_call in ai_msg.tool_calls: # type: ignore
                            tool_calls_in_answer.append(tool_call)

                    if len( tool_calls_in_answer ) == 0:
                        break

                    print( f"Executing tool calls: {len(tool_calls_in_answer)}" )
                    # Ha vannak tool hívások, mindegyiket végrehajtjuk
                    for tool_call in tool_calls_in_answer: # type: ignore
                        try:
                            await websocket.send_json({"type": "token", "content": f"*{tool_call['name']} hívása...* "})
                            await websocket.send_json({"type": "done"})
                        except:
                            break
                        
                        # MCP hívás
                        #print( "--------------------------" )
                        print( f"Calling tool {tool_call['name']}, params: {tool_call['args']}" )
                        observation = await call_mcp_tool(tool_call["name"], tool_call["args"], websocket, progress=lambda msg: websocket.send_json({"type": "token", "content": msg}) )
                        #print( f"Tool response {str(observation)}" )
                        #print( "" )
                        
                        # Visszajelzés a történetbe
                        t_msg = ToolMessage( content=str(observation), tool_call_id=tool_call["id"] )
                        messages.append( t_msg )
                        await session_data.addMessage( t_msg )

            except Exception as e:
                await websocket.send_json({"type": "error", "content": str(e)})
                break

    except Exception as e:
        print( "Session end:", str(e) )
    print( "Session terminated." );

if __name__ == "__main__":
    import uvicorn # type: ignore
    uvicorn.run(app, host=conf.get("webserver.listenaddress", "0.0.0.0"), port=conf.get("webserver.port" ) ) # type: ignore