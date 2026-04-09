from contextlib import asynccontextmanager
import json
import sys
from fastapi import FastAPI, WebSocket # type: ignore
import langchain
from mcp import ClientSession # type: ignore
from langchain_google_genai import ChatGoogleGenerativeAI # type: ignore
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage # type: ignore
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
                if k == "ID":
                    args2[ k ] = int(args[ '__arg%d' % cnt ])
                else:
                    args2[ k ] = args[ '__arg%d' % cnt ]
        finalArguments = args2
    return finalArguments


async def call_mcp_tool_streamableHttp(sessiondata, name: str, args: dict, websocket: WebSocket, toolparams: dict, progress=None ):
    try:
        sseClient = sessiondata.getConfiguredStreamableHttpClient( toolparams )

        async with sseClient as (read, write, _):
            async with ClientSession(read, write) as session:
                args = normalize_params( args, toolparams )
                await session.initialize()
                while True:
                    #if ( progress is not None ):
                    #    finalArguments['__progresscallback'] = progress
                    result = await session.call_tool( name, arguments=args )
                    if not "approvalToken" in toolparams['inputSchema']['properties']:
                        break
                    try:
                        decresult = json.loads( result.content[0].text ) # type: ignore
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


async def call_mcp_tool_sse(sessiondata, name: str, args: dict, websocket: WebSocket, toolparams: dict, progress=None ):
    try:
        sseClient = sessiondata.getConfiguredSSEClient( toolparams )

        async with sseClient as (read, write):
            async with ClientSession(read, write) as session:
                args = normalize_params( args, toolparams )
                await session.initialize()
                while True:
                    #if ( progress is not None ):
                    #    finalArguments['__progresscallback'] = progress
                    result = await session.call_tool( name, arguments=args )
                    if not "approvalToken" in toolparams['inputSchema']['properties']:
                        break
                    try:
                        decresult = json.loads( result.content[0].text ) # type: ignore
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


async def call_mcp_tool_jsonrpc(sessiondata, name: str, args: dict, websocket: WebSocket, toolparams: dict, progress=None ):
    try:
        jsonrpcClient = sessiondata.getConfiguredJsonRPCClient( toolparams )
        args = normalize_params( args, toolparams )
        result = await jsonrpcClient.toolCall( name, **args )
        return result
    except Exception as e:
        log.error(f"Tool not available: {e}")
    return f"Tool not found: '{name}'"


async def call_mcp_tool(sessiondata, name: str, args: dict, websocket: WebSocket, progress=None ):    
    toolparams = configmanager.get_params_for_tool( app.state.toolmap, name)

    if toolparams['proto'] == 'sse':
        return await call_mcp_tool_sse( sessiondata, name, args, websocket, toolparams, progress )

    elif toolparams['proto'] == 'jsonrpc':
        return await call_mcp_tool_jsonrpc( sessiondata, name, args, websocket, toolparams, progress )

    elif toolparams['proto'] == 'streamablehttp':
        return await call_mcp_tool_streamableHttp( sessiondata, name, args, websocket, toolparams, progress )



@app.get("/")
async def get():
    return FileResponse("www/index.html")


@app.get("/api/v1/chat/{sid}/history")
async def get_chat_history(sid: str):
    session_data = ChatSessionData( app )
    await session_data.load(sid)
    messages = []
    async for msg in session_data.getHistory():
        ( ls, content ) = await session_data.extractMemoryCommands( msg.content, 0, False )
        messages.append( {
            "type": msg.type,
            "content": content,
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

    messages = await session_data.getLastMessages( conf.get("chatparams.previousmessages", 15) ) # type: ignore
    messages.insert( 0, SystemMessage( content=await session_data.getCustomisedSystemPrompt() ) )
    try:
        while True:
            if ( len(session_data.messages) > 8 and session_data.name == "" and not title_generation ):
                await generate_title( llm, session_data, websocket )
                
            try:
                try:
                    user_text = await websocket.receive_text()
                    if ( user_text == "" ):
                        continue
                    user_msg = HumanMessage( content=user_text )
                    await session_data.saveMessage( user_msg )
                    messages.append( user_msg )
                except Exception as e:
                    break

                if len( session_data.messages) > 1:
                    while ( len(messages) > conf.get("chatparams.previousmessages", 15) ) or not isinstance(messages[1], HumanMessage): # type: ignore
                        messages.pop(1)  # Az első üzenet a rendszerüzenet, azt nem töröljük
                        if len(messages) <= 1:
                            break
                messages[0] = SystemMessage( content=await session_data.getCustomisedSystemPrompt() )

                toolCalls = 0
                while True:
                    toolCalls += 1
                    #ai_msg = await llm_with_tools.ainvoke( messages )

                    ai_msg = None
                    tool_calls_in_answer = []
                    lastcontent = ""
                    laststate = 0
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
                            if ( chunk.content[:-len(lastcontent)] == lastcontent ):
                                chunk.content = chunk.content[-len(lastcontent):] # type: ignore

                            ( laststate, lastcontent ) = await session_data.extractMemoryCommands( chunk.content, laststate, False ) # type: ignore
                            content = chunk.content # type: ignore
                            if content:
                                await websocket.send_json({"type": "token", "content": lastcontent } )

                    if ai_msg:
                        await websocket.send_json({"type": "done"})
                        await session_data.saveMessage( ai_msg )
                        await session_data.extractMemoryCommands( ai_msg.content, 0, True ) # type: ignore. 
                        # We do not use the output as we sent out the output in streaming mode, but we want to extract the memory 
                        # commands and execute them in the context of the full answer.
                        messages.append( ai_msg )
                
                    if len( tool_calls_in_answer ) == 0:
                        break

                    # Ha vannak tool hívások, mindegyiket végrehajtjuk
                    tcnt = 0
                    for tool_call in tool_calls_in_answer: # type: ignore                        
                        # MCP hívás
                        tcnt += 1
                        await websocket.send_json({"type": "toolcall", "content": f"[{tcnt}/{len(tool_calls_in_answer)}] {tool_call['name']}({tool_call['args']})"}) # type: ignore
                        observation = await call_mcp_tool( session_data, tool_call["name"], tool_call["args"], websocket, progress=lambda msg: websocket.send_json( { "type": "toolcall", "content": f"{msg}" } ) )
                        await websocket.send_json( {"type": "done" } ) # type: ignore
                        
                        # Visszajelzés a történetbe
                        t_msg = ToolMessage( content=str(observation), tool_call_id=tool_call["id"] )
                        await session_data.saveMessage( t_msg )
                        messages.append( t_msg )

                    if toolCalls > conf.get( "chatparams.max_tool_calls", 5 ): # type: ignore
                        await websocket.send_json({"type": "toolcall", "content": "**Túl sok eszköz hívás egy kérdésre.**"})
                        await websocket.send_json( {"type": "done" } ) # type: ignore
                        break

            except Exception as e:
                await websocket.send_json({"type": "error", "content": str(e)})
                break

    except Exception as e:
        print( "Session end:", str(e) )
    print( "Session terminated." );

if __name__ == "__main__":
    import uvicorn # type: ignore
    uvicorn.run(app, host=conf.get("webserver.listenaddress", "0.0.0.0"), port=conf.get("webserver.port" ) ) # type: ignore